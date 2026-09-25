package main

import "flag"

func main() {
	addr := flag.String("addr", ":8080", "listen address")
	var debug bool
	flag.BoolVar(&debug, "debug", false, "debug logging")
	flag.Parse()
	_ = addr
}
