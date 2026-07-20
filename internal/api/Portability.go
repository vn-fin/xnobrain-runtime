// <Summary>
// Portability exposes streamed .lumora export plus inspect, dry-run, and atomic
// apply endpoints. Uploaded bundles are validated before any profile is visible.
// File: Portability.go
// Functions: RegisterPortabilityRoutes, exportBundle, inspectBundle, dryRunBundle, applyBundle
// </Summary>
package api

import (
	"bufio"
	"bytes"
	"crypto/subtle"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/services/portability"
)

const bundleContentType = "application/vnd.open-lumora.bundle"

func (s *Server) RegisterPortabilityRoutes(app *fiber.App) {
	if s.portability == nil {
		return
	}
	root := app.Group("/api/v1/bundles")
	root.Post("/export", s.exportBundle)
	root.Post("/inspect", s.inspectBundle)
	root.Post("/dry-run", s.dryRunBundle)
	root.Post("/apply", s.applyBundle)
	if s.config.ManagedWorkerToken != "" {
		managed := app.Group("/internal/v1/imports")
		managed.Post("/:stage_id/stage", s.stageManagedBundle)
		managed.Post("/:stage_id/commit", s.commitManagedBundle)
		managed.Post("/:stage_id/rollback", s.rollbackManagedBundle)
	}
}

func (s *Server) stageManagedBundle(c fiber.Ctx) error {
	ownerID, err := s.authorizeManagedImport(c)
	if err != nil {
		return sendError(c, err)
	}
	return s.withBundle(c, func(_ string, reader io.ReaderAt, size int64) error {
		stage, err := s.portability.StageManaged(c.Context(), ownerID, c.Params("stage_id"), reader, size)
		if err != nil {
			return sendError(c, err)
		}
		return send(c, fiber.StatusCreated, stage, "managed bundle staged")
	})
}

func (s *Server) commitManagedBundle(c fiber.Ctx) error {
	ownerID, err := s.authorizeManagedImport(c)
	if err != nil {
		return sendError(c, err)
	}
	stage, err := s.portability.CommitManaged(c.Context(), ownerID, c.Params("stage_id"))
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, stage, "managed bundle committed")
}

func (s *Server) rollbackManagedBundle(c fiber.Ctx) error {
	ownerID, err := s.authorizeManagedImport(c)
	if err != nil {
		return sendError(c, err)
	}
	if err := s.portability.RollbackManaged(c.Context(), ownerID, c.Params("stage_id")); err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]bool{"rolled_back": true}, "managed bundle rolled back")
}

func (s *Server) authorizeManagedImport(c fiber.Ctx) (string, error) {
	provided := strings.TrimSpace(strings.TrimPrefix(c.Get(fiber.HeaderAuthorization), "Bearer "))
	expected := s.config.ManagedWorkerToken
	if provided == "" || len(provided) != len(expected) || subtle.ConstantTimeCompare([]byte(provided), []byte(expected)) != 1 {
		return "", fiber.NewError(fiber.StatusUnauthorized, "managed worker credential is invalid")
	}
	ownerID := strings.TrimSpace(c.Get("X-Lumora-Owner-ID"))
	if ownerID == "" || len(ownerID) > 256 || strings.ContainsAny(ownerID, "\r\n\x00") {
		return "", fiber.NewError(fiber.StatusBadRequest, "managed import owner is invalid")
	}
	return ownerID, nil
}

func (s *Server) exportBundle(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request struct {
		AgentIDs             []string `json:"agent_ids"`
		IncludeConversations bool     `json:"include_conversations"`
	}
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	runtimeRoot := filepath.Join(s.config.DataDir, "runtime")
	if err := os.MkdirAll(runtimeRoot, 0o750); err != nil {
		return sendError(c, err)
	}
	file, err := os.CreateTemp(runtimeRoot, ".bundle-export-*.lumora")
	if err != nil {
		return sendError(c, err)
	}
	name := file.Name()
	manifest, err := s.portability.Export(c.Context(), principal.UserID, portability.ExportOptions{AgentIDs: request.AgentIDs, IncludeConversations: request.IncludeConversations}, file)
	if err != nil {
		_ = file.Close()
		_ = os.Remove(name)
		return sendError(c, err)
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		_ = file.Close()
		_ = os.Remove(name)
		return sendError(c, err)
	}
	c.Set(fiber.HeaderContentType, bundleContentType)
	c.Set(fiber.HeaderContentDisposition, fmt.Sprintf(`attachment; filename="open-lumora-%s.lumora"`, manifest.ExportID))
	return c.SendStreamWriter(func(writer *bufio.Writer) {
		defer file.Close()
		defer os.Remove(name)
		_, _ = io.Copy(writer, file)
		_ = writer.Flush()
	})
}

func (s *Server) inspectBundle(c fiber.Ctx) error {
	return s.withBundle(c, func(_ string, reader io.ReaderAt, size int64) error {
		inspection, err := s.portability.Inspect(reader, size)
		if err != nil {
			return sendError(c, err)
		}
		return send(c, fiber.StatusOK, inspection, "bundle inspected successfully")
	})
}

func (s *Server) dryRunBundle(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	return s.withBundle(c, func(_ string, reader io.ReaderAt, size int64) error {
		report, err := s.portability.DryRun(c.Context(), principal.UserID, reader, size)
		if err != nil {
			return sendError(c, err)
		}
		return send(c, fiber.StatusOK, report, "bundle dry run completed successfully")
	})
}

func (s *Server) applyBundle(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	return s.withBundle(c, func(_ string, reader io.ReaderAt, size int64) error {
		report, err := s.portability.Apply(c.Context(), principal.UserID, reader, size)
		if err != nil {
			return sendError(c, err)
		}
		return send(c, fiber.StatusCreated, report, "bundle imported successfully")
	})
}

func (s *Server) withBundle(c fiber.Ctx, use func(filename string, reader io.ReaderAt, size int64) error) error {
	reader, size, filename, closeReader, err := requestBundle(c)
	if err != nil {
		return sendError(c, err)
	}
	if closeReader != nil {
		defer closeReader.Close()
	}
	return use(filename, reader, size)
}

func requestBundle(c fiber.Ctx) (io.ReaderAt, int64, string, io.Closer, error) {
	if strings.HasPrefix(strings.ToLower(c.Get(fiber.HeaderContentType)), "multipart/form-data") {
		header, err := c.FormFile("file")
		if err != nil {
			return nil, 0, "", nil, fmt.Errorf("bundle file is required")
		}
		file, err := header.Open()
		if err != nil {
			return nil, 0, "", nil, err
		}
		reader, ok := file.(io.ReaderAt)
		if !ok {
			_ = file.Close()
			return nil, 0, "", nil, fmt.Errorf("bundle upload is not seekable")
		}
		return reader, header.Size, filepath.Base(header.Filename), file, nil
	}
	payload := c.Body()
	if len(payload) == 0 {
		return nil, 0, "", nil, fmt.Errorf("bundle body is required")
	}
	reader := bytes.NewReader(payload)
	return reader, int64(reader.Len()), "bundle.lumora", nil, nil
}
