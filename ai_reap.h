#ifndef AI_REAP_H
#define AI_REAP_H

#include <sys/types.h>

/* Orphan reaping. ai is a child subreaper (prctl in main), so `nohup … &` jobs that tool commands
   leave behind are reparented to it and would linger as <defunct> once they finish.
   reap_children() collects every exited child (waitpid(-1, WNOHANG)); callers run it only at safe
   points where no popen()/system() child of this process is outstanding: each tick of the line
   editor and of the front-end's event loop. The child whose exit status matters (the Python
   engine) is registered with reap_track(); when the reaper collects it, the status is kept for
   reap_tracked() instead of being lost. reap_install_sigchld() adds a SIGCHLD handler (SA_RESTART,
   it does nothing itself) so that poll()/select() wake up and the next tick runs promptly. */
void reap_install_sigchld(void);
void reap_track(pid_t pid);
void reap_children(void);
int  reap_tracked(int *status);   /* 1 once the tracked child was reaped (*status set), else 0 */

#endif /* AI_REAP_H */
