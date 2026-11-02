package main

// Android (and other Linux builds): the app keeps a random 32-byte key in the
// phone's keystore-backed secure storage and hands it over in LINKTS_KEY. The
// state on disk is AES-256-GCM sealed with it, so the file alone is useless.

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"os"
)

type envSealer struct{ aead cipher.AEAD }

func newSealer() (sealer, error) {
	key, err := hex.DecodeString(os.Getenv("LINKTS_KEY"))
	if err != nil || len(key) != 32 {
		return nil, errors.New("LINKTS_KEY must be 32 bytes of hex")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return envSealer{aead}, nil
}

func (s envSealer) Seal(plain []byte) ([]byte, error) {
	nonce := make([]byte, s.aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return s.aead.Seal(nonce, nonce, plain, nil), nil
}

func (s envSealer) Open(sealed []byte) ([]byte, error) {
	n := s.aead.NonceSize()
	if len(sealed) < n {
		return nil, errors.New("sealed state too short")
	}
	return s.aead.Open(nil, sealed[:n], sealed[n:], nil)
}

// The app deletes and replaces the key itself on sign-out.
func (envSealer) Forget() error { return nil }
