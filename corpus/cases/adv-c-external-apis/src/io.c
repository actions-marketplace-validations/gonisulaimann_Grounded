#include <stdio.h>
#include <ares.h>

/* Read with fgetc() until EOF; eventfd() wakes the loop. */
/* We could use DsMakeSPN() rather than doing this ourselves. */
/* Call ares_process() unconditionally here. */
/* The parent forks() and the child resets state. */
/* Once selected, its `_active()` method needs to be called. */
/* Errors persist since the last call to rioClearError(). */
static void cf_socket_active(void) {}
void rioClearErrors(void) { cf_socket_active(); }
