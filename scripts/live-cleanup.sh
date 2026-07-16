#!/bin/sh

live_cleanup_done=0
live_cleanup_pid=
live_cleanup_pgid=
live_cleanup_temporary=
live_pending_signal=
live_pending_exit_code=

live_cleanup_init() {
    live_cleanup_pid=$1
    live_cleanup_pgid=$2
    live_cleanup_temporary=$3
}

live_group_alive() {
    [ "$live_cleanup_pgid" ] || return 1
    /bin/kill -0 -- "-$live_cleanup_pgid" 2>/dev/null
}

live_pid_alive() {
    [ "$live_cleanup_pid" ] || return 1
    /bin/kill -0 -- "$live_cleanup_pid" 2>/dev/null
}

live_cleanup() {
    [ "$live_cleanup_done" -eq 0 ] || return 0
    live_cleanup_done=1
    trap - EXIT HUP INT TERM

    # The shell can return from `setsid ... &` just before setsid establishes
    # the new process group. Give that short launch window time to settle.
    attempts=0
    while ! live_group_alive && live_pid_alive && [ "$attempts" -lt 50 ]; do
        attempts=$((attempts + 1))
        sleep 0.01
    done

    if live_group_alive; then
        /bin/kill -TERM -- "-$live_cleanup_pgid" 2>/dev/null || true
        attempts=0
        while live_group_alive && [ "$attempts" -lt 40 ]; do
            attempts=$((attempts + 1))
            sleep 0.05
        done
        if live_group_alive; then
            /bin/kill -KILL -- "-$live_cleanup_pgid" 2>/dev/null || true
        fi
    elif live_pid_alive; then
        # If setsid never established the group, the leader has not launched
        # descendants yet. Terminating that process closes the launch race.
        /bin/kill -TERM -- "$live_cleanup_pid" 2>/dev/null || true
    fi

    if [ "$live_cleanup_pid" ]; then
        wait "$live_cleanup_pid" 2>/dev/null || true
    fi
    if [ "$live_cleanup_temporary" ]; then
        rm -rf -- "$live_cleanup_temporary"
    fi
}

live_signal_exit() {
    signal_name=$1
    exit_code=$2
    live_cleanup
    trap - "$signal_name"
    exit "$exit_code"
}

live_defer_signal() {
    [ "$live_pending_signal" ] || {
        live_pending_signal=$1
        live_pending_exit_code=$2
    }
}

live_defer_traps() {
    trap live_cleanup EXIT
    trap 'live_defer_signal HUP 129' HUP
    trap 'live_defer_signal INT 130' INT
    trap 'live_defer_signal TERM 143' TERM
}

live_install_traps() {
    trap live_cleanup EXIT
    trap 'live_signal_exit HUP 129' HUP
    trap 'live_signal_exit INT 130' INT
    trap 'live_signal_exit TERM 143' TERM
}

live_activate_traps() {
    live_install_traps
    if [ "$live_pending_signal" ]; then
        live_signal_exit "$live_pending_signal" "$live_pending_exit_code"
    fi
}
