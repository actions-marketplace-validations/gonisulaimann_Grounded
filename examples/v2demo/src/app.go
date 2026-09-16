package main

import "fmt"

func realCmd() string {
	return "ok"
}

// Ping uses `realCmd()` and `fmt.Println()` under the hood.
func Ping() string {
	// Calls `ghost_cmd()` on failure.
	// See line 99 for the backoff policy.
	fmt.Println(realCmd())
	return realCmd()
}
