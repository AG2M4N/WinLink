package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"

	"tailscale.com/ipn"
)

func TestSecureStore(t *testing.T) {
	t.Setenv("LINKTS_KEYCHAIN_ACCOUNT", "link-store-test")
	dir := t.TempDir()
	secret := []byte(`{"PrivateNodeKey":"privkey:SECRET-NODE-KEY-123"}`)

	// Plain-text state from before encryption gets migrated, then deleted.
	os.WriteFile(filepath.Join(dir, "tailscaled.state"), []byte(`{"_machinekey":"`+"bWFjaGluZWtleQ=="+`"}`), 0o600)
	s, err := openSecureStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if v, _ := s.ReadState("_machinekey"); string(v) != "machinekey" {
		t.Fatalf("migration lost data: %q", v)
	}
	if _, err := os.Stat(filepath.Join(dir, "tailscaled.state")); !os.IsNotExist(err) {
		t.Fatal("plain-text file still there")
	}
	t.Log("migrated old plain-text file and deleted it")

	s.WriteState("profile-1", secret)
	raw, _ := os.ReadFile(filepath.Join(dir, sealedName))
	if bytes.Contains(raw, []byte("SECRET-NODE-KEY")) || bytes.Contains(raw, []byte("machinekey")) {
		t.Fatal("secret readable on disk")
	}
	if info, _ := os.Stat(filepath.Join(dir, sealedName)); info.Mode().Perm() != 0o600 {
		t.Fatalf("file permissions %v", info.Mode().Perm())
	}
	t.Logf("on disk: %d bytes, encrypted, permissions 600", len(raw))

	// "Restart": a new store reads the same sign-in back.
	s2, err := openSecureStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if v, _ := s2.ReadState("profile-1"); !bytes.Equal(v, secret) {
		t.Fatal("sign-in not restored after restart")
	}
	t.Log("restart: sign-in restored")

	// Sign out: everything gone, and new writes use a fresh key that still works.
	if err := s2.wipe(); err != nil {
		t.Fatal(err)
	}
	if _, err := s2.ReadState("profile-1"); err != ipn.ErrStateNotExist {
		t.Fatal("still signed in after wipe")
	}
	s2.WriteState("after", []byte("x"))
	s3, _ := openSecureStore(dir)
	if v, _ := s3.ReadState("after"); string(v) != "x" {
		t.Fatal("store unusable after sign-out")
	}
	t.Log("sign out: erased; new key works")

	// Key deleted behind our back: start fresh instead of failing.
	s3.seal.Forget()
	s4, err := openSecureStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s4.ReadState("after"); err != ipn.ErrStateNotExist {
		t.Fatal("expected a fresh start")
	}
	if _, err := os.Stat(filepath.Join(dir, sealedName+".unreadable")); err != nil {
		t.Fatal("unreadable file not kept aside")
	}
	t.Log("lost key: fresh start, old file kept aside")
	s4.seal.Forget() // clean up the test Keychain item
}
