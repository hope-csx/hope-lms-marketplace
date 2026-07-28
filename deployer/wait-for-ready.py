#!/usr/bin/env python3
"""Drop-in replacement for the Marketplace deployer's wait_for_ready.py that
explains *why* an install did not become ready.

Why this exists
───────────────
The base deployer image's wait_for_ready.py reports only that the application
missed its deadline. Everything that would identify the cause — container exit
codes, restart counts, and the logs of the container that failed — lives in the
cluster, and Marketplace deletes the namespace as soon as the task finishes. For
an automated test deployment run inside Google's infrastructure the namespace is
never reachable at all, so a failure yields a timeout message and nothing else.

This wrapper runs the original script unchanged and, only when it fails,
appends a summary of every pod that is not ready. The summary is raised as an
exception rather than merely printed because Marketplace's task report surfaces
tracebacks from the deployer log; text written outside one is not guaranteed to
appear in the report the producer sees.

Output is deliberately bounded (see MAX_DIAGNOSTIC_CHARS): the task report
truncates long deployer logs, and losing the tail would defeat the purpose.

FedRAMP: AU-2 / AU-3 (install failures are attributable), SI-11 (error handling
reports enough to diagnose without exposing secrets — only container status and
application logs are collected, never Secret or ConfigMap contents).
"""

import subprocess
import sys

#: The original script, renamed by Dockerfile.deployer.
REAL_SCRIPT = '/bin/wait_for_ready_real.py'

#: Upper bound on collected diagnostics. Marketplace truncates long logs.
MAX_DIAGNOSTIC_CHARS = 6000

#: Log lines to keep per failing container.
LOG_TAIL_LINES = 25


def kubectl(args, timeout=60):
    """Runs kubectl and returns its combined output, never raising.

    Diagnostics are best-effort: a failure to collect them must not mask the
    readiness failure that triggered collection.

    @param args - Arguments after `kubectl`
    @param timeout - Seconds to allow before giving up on the command
    @returns Combined stdout/stderr, or an explanatory message on failure
    """
    try:
        completed = subprocess.run(
            ['kubectl'] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return completed.stdout.decode('utf-8', 'replace').strip()
    except Exception as err:  # pylint: disable=broad-except
        return '<could not run kubectl {}: {}>'.format(' '.join(args), err)


def parse_namespace(argv):
    """Extracts the namespace from the argument list the base image passes.

    @param argv - Argument list, as given to this script
    @returns The namespace, or None when it is not present
    """
    for i, arg in enumerate(argv):
        if arg == '--namespace' and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith('--namespace='):
            return arg.split('=', 1)[1]
    return None


def container_summaries(pod):
    """Summarises the containers of one pod, newest failure first.

    Init containers are included because an application whose migrations cannot
    run never reaches its application containers at all, and that is invisible
    in the pod's own phase (it stays Pending).

    @param pod - Pod object as returned by `kubectl get pod -o json`
    @returns List of (container-name, is-init, one-line status) tuples
    """
    status = pod.get('status', {})
    rows = []
    for key, is_init in (('initContainerStatuses', True), ('containerStatuses', False)):
        for container in status.get(key, []):
            state = container.get('state', {})
            last = container.get('lastState', {}).get('terminated', {})
            phase = next(iter(state), 'unknown')
            detail = state.get(phase, {})
            summary = '{}={}'.format(phase, detail.get('reason') or detail.get('message') or 'ok')
            if 'exitCode' in detail:
                summary += ' exit={}'.format(detail['exitCode'])
            if last:
                summary += ' lastExit={} ({})'.format(
                    last.get('exitCode'), last.get('reason')
                )
            summary += ' restarts={} ready={}'.format(
                container.get('restartCount', 0), container.get('ready')
            )
            rows.append((container.get('name'), is_init, summary))
    return rows


def collect(namespace):
    """Builds a bounded report of everything in the namespace that is not ready.

    @param namespace - Namespace the application was installed into
    @returns Human-readable diagnostic text
    """
    import json  # Imported lazily: unused on the success path.

    parts = ['Pods in namespace {}:'.format(namespace), kubectl(
        ['get', 'pods', '--namespace', namespace, '-o', 'wide'])]

    raw = kubectl(['get', 'pods', '--namespace', namespace, '-o', 'json'])
    try:
        pods = json.loads(raw).get('items', [])
    except ValueError:
        return '\n'.join(parts + ['<could not parse pod list>'])

    for pod in pods:
        name = pod.get('metadata', {}).get('name', '?')
        conditions = {
            c.get('type'): c.get('status') for c in pod.get('status', {}).get('conditions', [])
        }
        if conditions.get('Ready') == 'True':
            continue

        parts.append('')
        parts.append('── {} (phase={}) ──'.format(name, pod.get('status', {}).get('phase')))

        for container, is_init, summary in container_summaries(pod):
            if is_init:
                parts.append('  initContainer {}: {}'.format(container, summary))
            else:
                parts.append('  container {}: {}'.format(container, summary))

        # Log the container that is blocking the pod. A pod stuck on an init
        # container has no useful application log yet, so prefer the first init
        # container that is not ready.
        blocking = [(c, i) for c, i, _ in container_summaries(pod)
                    if not _container_ready(pod, c)]
        for container, is_init in blocking[:2]:
            for previous in (True, False):
                args = ['logs', name, '--namespace', namespace, '--container', container,
                        '--tail={}'.format(LOG_TAIL_LINES)]
                if previous:
                    args.append('--previous')
                out = kubectl(args)
                # kubectl answers with a placeholder rather than an error when a
                # container has not started, which says nothing the status line
                # above has not already said and would consume the output cap.
                if out and not _is_placeholder(out):
                    parts.append('  {} log of {}{}:'.format(
                        'previous' if previous else 'current',
                        'init container ' if is_init else '', container))
                    parts.extend('    ' + line for line in out.splitlines())

    text = '\n'.join(parts)
    if len(text) > MAX_DIAGNOSTIC_CHARS:
        text = text[:MAX_DIAGNOSTIC_CHARS] + '\n<diagnostics truncated>'
    return text


def _is_placeholder(output):
    """Reports whether kubectl returned a stand-in instead of container output.

    @param output - Combined output of a `kubectl logs` invocation
    @returns True when the output carries no log content
    """
    return (
        output.startswith('Error from server')
        or 'is waiting to start' in output
        or 'not found' in output
        or 'previous terminated' in output
    )


def _container_ready(pod, container_name):
    """Reports whether a named container of a pod is ready.

    @param pod - Pod object as returned by `kubectl get pod -o json`
    @param container_name - Container to look up
    @returns True when the container reports ready
    """
    status = pod.get('status', {})
    for key in ('initContainerStatuses', 'containerStatuses'):
        for container in status.get(key, []):
            if container.get('name') == container_name:
                return bool(container.get('ready'))
    return False


def main():
    """Runs the real readiness wait, adding diagnostics when it fails.

    @raises Exception When the application did not become ready; the message
        carries the collected diagnostics so they reach the task report
    """
    code = subprocess.call([REAL_SCRIPT] + sys.argv[1:])
    if code == 0:
        sys.exit(0)

    namespace = parse_namespace(sys.argv)
    diagnostics = collect(namespace) if namespace else '<namespace unknown>'

    # Printed as well as raised: `kubectl logs` on the deployer job is the more
    # convenient path for anyone who still has the cluster.
    print(diagnostics)
    sys.stdout.flush()

    raise Exception(
        'ERROR Application did not become ready. Diagnostics follow.\n' + diagnostics)


if __name__ == '__main__':
    main()
