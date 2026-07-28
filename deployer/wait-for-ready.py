#!/usr/bin/env python3
"""Drop-in replacement for the Marketplace deployer's wait_for_ready.py that
explains *why* an install did not become ready.

Why this exists
───────────────
The base deployer image's wait_for_ready.py reports only that the application
missed its deadline. Everything that would identify the cause — container exit
codes, restart counts, and the logs of the container that failed — lives in the
cluster, and Marketplace deletes the namespace as soon as the task ends. For an
automated test deployment run inside Google's infrastructure the namespace is
never reachable at all, so a failure yields a timeout message and nothing else.

How the diagnostics get out
───────────────────────────
Two channels, because the useful one is not obvious:

1. **Kubernetes events.** Marketplace's task report reproduces the namespace's
   Warning events in full, so a Warning event per failing container is the only
   channel guaranteed to reach the producer. This is what actually works.
2. **The exception message.** The report also quotes tracebacks from the deployer
   log, but only the first one it finds — which would otherwise be the wrapped
   script's own timeout traceback, carrying nothing. The wrapped script's output
   is therefore streamed with its traceback marker rewritten, leaving this
   script's traceback as the only one the report can quote.

Both are bounded: the report truncates, so losing the tail would defeat the
purpose. See ADR 0031.

FedRAMP: AU-2 / AU-3 (install failures are attributable), SI-11 (error handling
reports enough to diagnose without exposing secrets — only container status and
application logs are collected, never Secret or ConfigMap contents).
"""

import datetime
import json
import os
import subprocess
import sys

#: The original script, renamed by Dockerfile.deployer.
REAL_SCRIPT = '/bin/wait_for_ready_real.py'

#: Marker rewritten in the wrapped script's output so that this script's
#: traceback is the one the task report quotes.
TRACEBACK_MARKER = 'Traceback (most recent call last):'

#: Upper bound on the diagnostics carried in the exception message.
MAX_DIAGNOSTIC_CHARS = 2500

#: Upper bound on the log excerpt carried in a single event message. Events are
#: printed one per line in the report, so this trades width for certainty.
MAX_EVENT_LOG_CHARS = 700

#: Log lines to collect per failing container.
LOG_TAIL_LINES = 20

#: Most events to publish. A failing install usually has one real cause; the
#: rest are pods waiting on it.
MAX_EVENTS = 6


def kubectl(args, stdin=None, timeout=60):
    """Runs kubectl and returns its combined output, never raising.

    Diagnostics are best-effort: a failure to collect or publish them must not
    mask the readiness failure that triggered collection.

    @param args - Arguments after `kubectl`
    @param stdin - Optional string piped to the command
    @param timeout - Seconds to allow before giving up on the command
    @returns Combined stdout/stderr, or an explanatory message on failure
    """
    try:
        completed = subprocess.run(
            ['kubectl'] + args,
            input=stdin.encode('utf-8') if stdin else None,
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


def is_placeholder(output):
    """Reports whether kubectl returned a stand-in instead of container output.

    @param output - Combined output of a `kubectl logs` invocation
    @returns True when the output carries no log content
    """
    return (
        not output
        or output.startswith('Error from server')
        or 'is waiting to start' in output
        or 'previous terminated' in output
        # Emitted by the kubelet when the log of the requested run has already
        # been reclaimed, which happens to a container that has restarted several
        # times. It reads like content but carries none.
        or 'unable to retrieve container logs' in output
        or output.startswith('<could not run kubectl')
    )


def describe_state(container):
    """Summarises one container status as a single line.

    @param container - Entry of a pod's (init)containerStatuses
    @returns Human-readable status, including why a previous run ended
    """
    state = container.get('state', {})
    phase = next(iter(state), 'unknown')
    detail = state.get(phase, {})
    summary = '{}={}'.format(phase, detail.get('reason') or detail.get('message') or 'ok')
    if 'exitCode' in detail:
        summary += ' exit={}'.format(detail['exitCode'])
    last = container.get('lastState', {}).get('terminated', {})
    if last:
        summary += ' lastExit={} ({})'.format(last.get('exitCode'), last.get('reason'))
    return summary + ' restarts={} ready={}'.format(
        container.get('restartCount', 0), container.get('ready')
    )


def container_log(namespace, pod_name, container, previous):
    """Reads the tail of one container's log.

    The previous run is preferred for a restarting container: its log is the one
    that recorded the failure, while the current run has usually only just
    started.

    @param namespace - Namespace of the pod
    @param pod_name - Pod to read from
    @param container - Container within the pod
    @param previous - Read the prior terminated run instead of the current one
    @returns Log tail, or None when there is nothing to read
    """
    args = [
        'logs', pod_name, '--namespace', namespace, '--container', container,
        '--tail={}'.format(LOG_TAIL_LINES),
    ]
    if previous:
        args.append('--previous')
    out = kubectl(args)
    return None if is_placeholder(out) else out


def own_job_name(namespace):
    """Identifies the Job this script is running as part of.

    The deployer runs as a Job with a retry budget, so by the time it gives up
    its own earlier attempts are sitting in the namespace as failed pods. Left
    in, they crowd out the application's failure — and because each one contains
    the previous attempt's diagnostics, they nest.

    @param namespace - Namespace the deployer is running in
    @returns The Job name, or None when it cannot be determined
    """
    pod_name = os.environ.get('HOSTNAME')
    if not pod_name:
        return None
    out = kubectl([
        'get', 'pod', pod_name, '--namespace', namespace,
        '-o', 'jsonpath={.metadata.labels.job-name}',
    ])
    return out or None


def failing_containers(namespace, exclude_job=None):
    """Finds every container holding the install back, with its log.

    Init containers are reported before application containers: a pod whose
    migrations cannot run never reaches its application containers, so the init
    container is the cause and the rest are consequences. Ordering matters
    because the task report truncates.

    @param namespace - Namespace the application was installed into
    @param exclude_job - Job whose pods to ignore, normally the deployer's own
    @returns List of dicts describing each not-ready container, causes first
    """
    raw = kubectl(['get', 'pods', '--namespace', namespace, '-o', 'json'])
    try:
        pods = json.loads(raw).get('items', [])
    except ValueError:
        return []

    found = []
    for pod in pods:
        meta = pod.get('metadata', {})
        status = pod.get('status', {})
        conditions = {c.get('type'): c.get('status') for c in status.get('conditions', [])}
        if conditions.get('Ready') == 'True':
            continue
        if exclude_job and meta.get('labels', {}).get('job-name') == exclude_job:
            continue

        for key, is_init in (('initContainerStatuses', True), ('containerStatuses', False)):
            for container in status.get(key, []):
                if container.get('ready'):
                    continue
                name = container.get('name')
                log = None
                if container.get('restartCount'):
                    log = container_log(namespace, meta.get('name'), name, previous=True)
                if log is None:
                    log = container_log(namespace, meta.get('name'), name, previous=False)
                found.append({
                    'pod': meta.get('name'),
                    'uid': meta.get('uid'),
                    'container': name,
                    'is_init': is_init,
                    'phase': status.get('phase'),
                    'status': describe_state(container),
                    'log': log,
                })

    found.sort(key=lambda failure: (not failure['is_init'], failure['log'] is None))
    return found


def publish_events(namespace, failures):
    """Records one Warning event per failing container.

    Marketplace's task report lists the namespace's Warning events verbatim,
    which makes this the only diagnostic channel that reliably reaches the
    producer of a release whose test namespace they cannot see.

    @param namespace - Namespace to create the events in
    @param failures - Entries from failing_containers()
    @returns Number of events successfully created
    """
    created = 0
    now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    for failure in failures[:MAX_EVENTS]:
        log = (failure['log'] or '<no log output>').replace('\n', ' | ')
        if len(log) > MAX_EVENT_LOG_CHARS:
            log = log[-MAX_EVENT_LOG_CHARS:]
        message = '{}{} in {}: {}. Last log: {}'.format(
            'init container ' if failure['is_init'] else 'container ',
            failure['container'], failure['pod'], failure['status'], log,
        )

        event = {
            'apiVersion': 'v1',
            'kind': 'Event',
            'metadata': {
                'generateName': 'hope-lms-install-diagnostics-',
                'namespace': namespace,
            },
            'type': 'Warning',
            'reason': 'InstallDiagnostics',
            'message': message,
            # Without these the event has no age, and `kubectl get events` — the
            # view the task report reproduces — renders LAST SEEN as <unknown>.
            'firstTimestamp': now,
            'lastTimestamp': now,
            'count': 1,
            'involvedObject': {
                'apiVersion': 'v1',
                'kind': 'Pod',
                'name': failure['pod'],
                'namespace': namespace,
                'uid': failure['uid'],
            },
            'source': {'component': 'hope-lms-deployer'},
        }
        out = kubectl(['create', '--namespace', namespace, '-f', '-'],
                      stdin=json.dumps(event))
        if 'created' in out:
            created += 1
        else:
            print('could not publish diagnostic event: {}'.format(out))
    return created


def format_diagnostics(namespace, failures):
    """Renders the diagnostics carried in the exception message.

    @param namespace - Namespace the application was installed into
    @param failures - Entries from failing_containers()
    @returns Bounded, human-readable diagnostic text
    """
    if not failures:
        return 'No not-ready containers found in {}. Pods:\n{}'.format(
            namespace, kubectl(['get', 'pods', '--namespace', namespace, '-o', 'wide']))

    parts = []
    for failure in failures:
        parts.append('── {}{} in {} (pod phase={}) ──'.format(
            'init container ' if failure['is_init'] else 'container ',
            failure['container'], failure['pod'], failure['phase']))
        parts.append('   {}'.format(failure['status']))
        for line in (failure['log'] or '<no log output>').splitlines():
            parts.append('   | {}'.format(line))

    text = '\n'.join(parts)
    if len(text) > MAX_DIAGNOSTIC_CHARS:
        text = text[:MAX_DIAGNOSTIC_CHARS] + '\n<truncated>'
    return text


def run_wrapped(argv):
    """Runs the original readiness wait, streaming its output.

    The traceback marker is rewritten as the output streams so that this
    script's traceback is the only one the task report can quote. Nothing else
    about the output changes.

    @param argv - Arguments to forward to the wrapped script
    @returns The wrapped script's exit code
    """
    # Unbuffered, because reading the child through a pipe otherwise switches its
    # stdout to block buffering and its four-second progress reports would not
    # appear until it exited — fifteen minutes of silence during a slow install.
    child_env = dict(os.environ, PYTHONUNBUFFERED='1')
    process = subprocess.Popen(
        [REAL_SCRIPT] + argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=child_env,
    )
    for raw in process.stdout:
        line = raw.decode('utf-8', 'replace').rstrip('\n')
        print(line.replace(TRACEBACK_MARKER, 'wait_for_ready trace:'))
        sys.stdout.flush()
    return process.wait()


def main():
    """Runs the real readiness wait, adding diagnostics when it fails.

    @raises Exception When the application did not become ready; the message
        carries the collected diagnostics so they reach the task report
    """
    code = run_wrapped(sys.argv[1:])
    if code == 0:
        sys.exit(0)

    namespace = parse_namespace(sys.argv)
    if not namespace:
        raise Exception('ERROR Application did not become ready, and the '
                        'namespace could not be determined for diagnostics.')

    failures = failing_containers(namespace, exclude_job=own_job_name(namespace))
    published = publish_events(namespace, failures)
    diagnostics = format_diagnostics(namespace, failures)

    print('Pods in namespace {}:'.format(namespace))
    print(kubectl(['get', 'pods', '--namespace', namespace, '-o', 'wide']))
    print(diagnostics)
    print('Published {} diagnostic event(s).'.format(published))
    sys.stdout.flush()

    raise Exception(
        'ERROR Application did not become ready. {} container(s) not ready; '
        'see InstallDiagnostics events.\n{}'.format(len(failures), diagnostics))


if __name__ == '__main__':
    main()
