# Runtime one-file contract

```text
builder interpreter: /usr/local/lib/hermes-agent/venv/bin/python
entry:               server.py
output:              /opt/xnobrain-dist/xnobrain-runtime
runtime executable:  /usr/local/bin/app.so
launcher:            runtime/container-entrypoint.sh -> exec app.so
```

The executable embeds XNOBrain and API-process imports. The image intentionally
retains the Hermes virtual environment and other non-API tools because agents,
skill synchronization, and office workflows execute independently. Validate
both Docker startup and an Incus target when managed image behavior changes.
