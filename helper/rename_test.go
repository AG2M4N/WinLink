//go:build darwin

package main

import (
	"bytes"
	"encoding/hex"
	"testing"
)

func TestKeychainRenameKeepsKey(t *testing.T) {
	t.Setenv("LINKTS_KEYCHAIN_ACCOUNT", "rename-test")
	old := bytes.Repeat([]byte{7}, 32)
	security("add-generic-password", "-U", "-A", "-s", oldKeychainService, "-a", "rename-test", "-w", hex.EncodeToString(old))
	defer security("delete-generic-password", "-s", keychainService, "-a", "rename-test")
	defer security("delete-generic-password", "-s", oldKeychainService, "-a", "rename-test")

	key, err := loadOrCreateKey()
	if err != nil || !bytes.Equal(key, old) {
		t.Fatalf("expected the old key to be reused, got %x %v", key, err)
	}
	if _, err := security("find-generic-password", "-s", oldKeychainService, "-a", "rename-test"); err == nil {
		t.Fatal("old Keychain item still there")
	}
	if out, err := security("find-generic-password", "-s", keychainService, "-a", "rename-test", "-w"); err != nil || out != hex.EncodeToString(old) {
		t.Fatal("key not saved under the new name")
	}
	t.Log("old 'Link Tailscale key' moved to 'WinLink Tailscale key', same key")
}
