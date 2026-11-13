# Private Azure learning demo

This is a local and SSH-tunnel learning configuration, not a production
setup. The local and Azure synthetic-data journeys described below have been
demonstrated; a repeatable fresh-machine setup has not been verified. Use only
synthetic data and an organisation-approved environment.

The frontend, API and Keycloak host ports bind to loopback. The API and optional
MCP service share Keycloak's network namespace so that their signing-key URL can
use container loopback without relaxing the application's URL validation.
`start-dev` is restricted to this private test; it is not a public Keycloak
deployment. The `mcp` profile is optional and has not had an authenticated
container tool-call test.

Compose creates separate project-scoped named volumes for SQLite and Keycloak.
The storage initializer changes ownership of those named-volume roots for the
respective container users. Never substitute a host directory or an existing
volume without reviewing its ownership and backup requirements. The migration
service is an explicit `setup` profile: it is not run automatically at startup.
Run a migration only against an explicitly selected database after checking its
state and backup. The API's `/ready` endpoint checks connectivity, not whether
the expected tables or migrations are present.

The Keycloak realm, public login client, API audience mapper, roles and synthetic
users need to be configured or imported into a new instance. A private existing realm export
must not be copied into the repository. Keycloak uses a named volume containing
credential and signing-key material; protect it. Keycloak recovery from that
volume has not yet been demonstrated for this configuration.

Supply `KC_BOOTSTRAP_ADMIN_USERNAME` and `KC_BOOTSTRAP_ADMIN_PASSWORD` privately
in the shell process before using Compose. Values are visible to container
management while the container exists. Do not paste them into chat, files,
command history, logs or Git. Use `--env-file deploy/demo.env.example` when
invoking Compose to select the non-secret defaults file rather than implicitly
loading a root `.env`; never print resolved Compose configuration containing
secrets. The browser configuration in `frontend-config.js` is public.

## Verified local evidence

On 12 November 2026, Daniel ran the following checks with Podman on Windows:

- Compose configuration parsing passed for the setup and MCP profiles.
- The API and frontend images built using direct `podman build` commands.
- A new Compose project's volume initializer and explicit Alembic migration
  completed successfully on a disposable SQLite database.
- Keycloak, the API and the frontend started. Daniel signed in as a synthetic
  requester, created a ticket and confirmed it remained visible after an API
  container restart.
- SQLite's online backup API copied the test database to a separate volume.
  A restore to another volume passed `PRAGMA integrity_check`, contained four
  tickets and recorded migration revision `0f96f8423f48`.

These results apply to that local test. The backup
and restored copy were on the same Podman machine; this does not protect
against loss of the machine and does not back up Keycloak.

The Compose provider's build command failed while checking the local
`backend/.pytest_cache` directory. Its cause is unconfirmed. Direct Podman
builds succeeded, but a fresh-clone Compose build remains unverified.

## Verified Azure demo

On 12–13 November 2026, Daniel manually deployed a synthetic-data demo to
an Azure for Students Ubuntu 24.04 VM. The VM has a public IP with SSH limited
to his current public IP. The application ports are bound to VM loopback and
accessed from his PC through an SSH tunnel. This is a private development demo,
not a production setup. No automatic deployment was used.

- Rootless Podman 4.9.3 built the API image. The frontend image built after
  specifying the fully qualified Docker Hub address for its Nginx base image.
  The VM build used a temporary Dockerfile; the repository Dockerfile has since
  been updated locally and still needs its own verification and commit.
- Ubuntu's podman-compose 1.0.6 could parse the file but did not accept the
  `--profile` option. Its dependency-condition behaviour was not demonstrated.
  Daniel used a separately installed Docker Compose standalone 5.7.0 binary
  with the rootless Podman socket instead. This provider and setup are specific
  to the demonstrated VM and are not a verified installation recipe for others.
- Using a new Compose project, the storage initializer completed and Alembic
  migrated the new SQLite database to revision `0f96f8423f48`. The API, frontend
  and Keycloak containers started. The SQLite and Keycloak data use separate
  named volumes on the VM.
- Daniel imported his confirmed synthetic local Keycloak realm into the new
  Azure instance, configured the UI redirect and post-logout redirect for the
  tunnel, and verified sign-in and logout. Realm exports contain sensitive
  identity and signing-key material and must not be committed or shared.
- Through the application, Daniel created a test category and ticket. He
  confirmed requester isolation, assignment after the agent's first sign-in,
  and that the requester saw a public comment but not an internal comment.
  After restarting the API, readiness returned `ready`; Daniel confirmed the
  closed ticket and public comment remained visible.

The containers required manual starting after the VM was restarted; they were
also found stopped after an earlier session ended. The cause is unconfirmed. Automatic startup,
behaviour after a VM reboot and deployment without an interactive session are
not yet verified. The original frontend build on the VM failed because its
unqualified Nginx image name did not resolve. The app's authenticated MCP path,
VM backup/restore, identity recovery and a full fresh-machine run remain untested.
The Azure demo has not been deployed or updated automatically by GitHub.

## Resuming the Azure demo

The demonstrated setup uses an SSH tunnel for browser access to VM loopback
ports 18181 (frontend), 18182 (Keycloak) and 18183 (API). Before connecting,
check that those ports are free on the PC and that the VM's SSH security rule
allows the current public IP. A changed network can change that IP. Do not open
the application ports publicly for this development configuration.

After the VM is started, Daniel connected over SSH and manually started the
existing Keycloak, API and frontend containers, in that order. Keep the VM SSH
session open during the demo, and use a separate local SSH tunnel. Check service
readiness before opening the frontend. These are observations for the existing
demo project, not proof of a reliable unattended startup procedure.

When finished, stop the VM in Azure and confirm **Stopped (deallocated)** to
stop VM compute billing. Retained disks and a public IP can continue to incur
charges. The VM's OS disk was configured to remain when the VM is deleted;
it contains the rootless Podman volumes. Stopping, deallocation, deletion and
volume removal are different actions: do not delete resources or volumes as a
shortcut for stopping them.

## Running the private demo

The following is a draft runbook based on the local test; the entire sequence
has not yet been verified from a fresh clone. Run commands yourself in
PowerShell from the repository root, using an approved Podman installation and
Compose provider. The first build may contact approved image and package
registries. Do not place real support data in this demo.

Build the images directly. The explicit names are the local tags used by
`compose.azure-demo.yml`:

```powershell
podman build --tag localhost/helpdesk-api:azure-demo backend
podman build --tag localhost/helpdesk-frontend:azure-demo frontend
```

For a **new disposable test**, choose a unique Compose project name and
record it. Before continuing, check that neither its `helpdesk-data` nor its
`keycloak-data` volume already exists. Stop if either exists or if the check
fails; do not reuse or delete another volume. Compose normally prefixes the
volume names with the project name and an underscore. Verify the names for the
provider actually in use.

Set the bootstrap username and password privately in the current PowerShell
process. The username can be `demo-admin`; prompt for a new temporary-use
password with `Read-Host -AsSecureString` and pass it through the process
environment. Do not enter the password as a literal command. Keep it private
for the one-time administration setup. The shell variables should be removed
or restored after use; the container retains its environment metadata while it
exists.

With the intended project name and credentials set, the initial sequence is:

1. Explicitly run the `migrate` service in the `setup` profile against the
   new disposable volumes. Confirm it completed before starting the app.
2. Start the stack without rebuilding. Wait for Keycloak, not just the
   containers, to become ready.
3. In the new Keycloak instance, create the `helpdesk` realm, the requester,
   agent and admin roles, the `helpdesk-api` audience client and the public
   `helpdesk-ui` client with S256 PKCE. Add an access-token audience mapper
   for `helpdesk-api`, and configure the exact frontend redirect and origin
   `http://127.0.0.1:18181/` and `http://127.0.0.1:18181` respectively.
4. Create a synthetic requester and a private temporary-use password. Seed
   only the new migrated demo database if sample categories are needed.
5. Open `http://127.0.0.1:18181/`, sign in, create a synthetic ticket and
   check that it remains after a restart.

The demo's Keycloak issuer is `http://127.0.0.1:18182/realms/helpdesk`;
the API retrieves signing keys at container loopback port 8080. The frontend
calls the API at `http://127.0.0.1:18183`. A remote VM requires approved SSH
tunnels for the browser-facing ports and further verification; the local
addresses do not make the services publicly accessible.

## Backup, recovery and cleanup

Use SQLite's online backup API for a consistent snapshot; do not copy a live
database file blindly. The demonstrated restore used a new, separate volume
and checked integrity, ticket count and migration revision. For real recovery,
decide where encrypted or otherwise protected off-machine backups belong,
exercise restoration and agree retention. Do not overwrite the active database.

Keycloak has separate identity data. Its backup and recovery need their own
approved, tested procedure. Keycloak documents limitations of realm exports as
backups: https://www.keycloak.org/server/importExport. Exports may contain
private material and must not be committed or shared.

Do not run `down --volumes`, remove test volumes or overwrite an existing
database as a cleanup shortcut. Image publication, automated deployment,
authenticated MCP and full recovery remain unverified.

## Draft GitHub continuous deployment

The workflow contains a disabled-by-default publishing and deployment path.
It runs only for a push to `master`, after the backend, frontend and image-build
jobs pass, and only when the repository variable `HELPDESK_CD_ENABLED` is exactly
`true`. Pull-request jobs remain on GitHub-hosted runners. Do not enable the
variable until the configuration, runner trust and a disposable end-to-end test
have been reviewed. This draft has not yet published images or deployed a VM.

The publish job builds API and frontend images and pushes them to GitHub
Container Registry using commit-SHA tags. Tags identify a commit but can be
overwritten; they are not immutable digests. Package visibility and repository
access must be checked before publishing or pulling private images. The workflow
uses the job's short-lived `GITHUB_TOKEN`, not a stored personal access token.

The deployment job requires a trusted self-hosted Linux x64 runner labelled
`helpdesk-azure-demo`, running as the same VM user that owns the existing
rootless Podman containers. That runner needs PowerShell (`pwsh`), Podman,
outbound access to GitHub and GHCR, and the previously verified standalone
Compose provider. Daniel installed PowerShell 7.7.0 and registered the runner
on 13 November 2026; unattended runner startup has not been verified. A runner can execute repository
workflow code on the VM: restrict repository and workflow access to trusted
people. The branch/event guard in this workflow alone does not prevent a
different workflow from targeting the runner.

Set repository variables `HELPDESK_DEPLOY_DIR` to the absolute path of the
existing demo configuration and `HELPDESK_COMPOSE_BIN` to the absolute path
of the verified Compose executable. The runner must have access to the same
rootless Podman socket and volumes as the demo user. The deployment script targets
the runner user's socket at `/run/user/<uid>/podman/podman.sock`; the socket must
be active before the job starts. If the VM or runner is
offline, jobs will not deploy until a runner becomes available; they may queue
or expire. VM deallocation stops compute charges but does not run deployments.

The draft deployment script checks that the expected containers and database
volume exist, that the installed non-secret configuration matches the checked
out commit, and that the commit is still the current `master` when checked. It
pulls both images, checks the database against Alembic heads using a read-only
volume mount, and refuses to run a migration automatically. It then replaces only
the API and frontend with Compose `--no-deps`, leaving Keycloak and both volumes
in place, and checks the API readiness and frontend HTTP response. The script
records the prior image IDs in the job log. These checks and update semantics
have not yet been tested on this VM or against a disposable copy.

The workflow has no automatic rollback. An interrupted or failed update can
leave changed tags or partially replaced containers and requires inspection and
manual recovery. Database and Keycloak backup/restore, including off-machine
storage, must be planned and tested independently. A readiness response only
checks the endpoint; it does not prove that sign-in or ticket workflows work.
The `master` check reduces stale deployments but is not an atomic guarantee if
the branch changes after the check. Do not enable CD until these limitations
and the repository's runner and package permissions are understood.
