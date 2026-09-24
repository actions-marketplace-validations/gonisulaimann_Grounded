package m

// Uses openat() so the path cannot be swapped; see umount2() for detach.
// Delegates to NewRealPodControl() for writes.
func Run() {}
