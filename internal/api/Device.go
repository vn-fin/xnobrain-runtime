// <Summary>
// Device exposes optional cloud-connector health and local unpair. It never
// returns access tokens, signing keys, command payloads, or profile content.
// File: Device.go
// Functions: RegisterDeviceRoutes, deviceStatus, unpairDevice
// </Summary>
package api

import (
	"context"
	"fmt"

	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/services/deviceconnector"
)

type managedDeviceConnector interface {
	Status() deviceconnector.Status
	Pair(context.Context) error
	Unpair(context.Context) error
}

func (s *Server) RegisterDeviceRoutes(app *fiber.App) {
	app.Get("/api/v1/device", s.deviceStatus)
	app.Post("/api/v1/device/pair", s.pairDevice)
	app.Post("/api/v1/device/unpair", s.unpairDevice)
}

func (s *Server) pairDevice(c fiber.Ctx) error {
	connector, ok := s.connector.(managedDeviceConnector)
	if !ok {
		return sendError(c, fmt.Errorf("device cloud connection is not configured"))
	}
	if err := connector.Pair(c.Context()); err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusAccepted, map[string]bool{"pairing": true}, "device pairing started")
}

func (s *Server) deviceStatus(c fiber.Ctx) error {
	connector, ok := s.connector.(managedDeviceConnector)
	if !ok {
		return send(c, fiber.StatusOK, deviceconnector.Status{Enabled: false}, "device cloud connection is disabled")
	}
	return send(c, fiber.StatusOK, connector.Status(), "device status retrieved successfully")
}

func (s *Server) unpairDevice(c fiber.Ctx) error {
	connector, ok := s.connector.(managedDeviceConnector)
	if !ok {
		return send(c, fiber.StatusOK, map[string]bool{"unpaired": true}, "device cloud connection is already disabled")
	}
	if err := connector.Unpair(c.Context()); err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]bool{"unpaired": true}, "device unpaired successfully")
}
