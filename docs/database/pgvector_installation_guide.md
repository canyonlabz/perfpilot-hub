# Using pgvector with Docker

**pgvector** is an open-source extension for PostgreSQL that enables vector similarity search. It supports various distance metrics and vector types, making it a powerful tool for applications requiring nearest neighbor search. You can easily install and use pgvector with Docker.

> **Prerequisite:** A Docker-compatible container runtime must be installed and running.
> - **Rancher Desktop** (enterprise/team use): https://rancherdesktop.io/
> - **Docker Desktop** (personal use): https://www.docker.com/products/docker-desktop/

## Step 1: Installation with Docker

To install pgvector using Docker, you can pull the Docker image that includes pgvector and PostgreSQL. Here is how you can do it:

```bash
# Pull the Docker image with pgvector
docker pull pgvector/pgvector:pg18
```

## Step 2: Running the PostgreSQL Container

### Method 1: Run container directly via `docker run`

Once you have the Docker image, you can run a container with pgvector and PostgreSQL:

```bash
docker run -e POSTGRES_USER=perfadmin \
           -e POSTGRES_PASSWORD=mypassword \
           -e POSTGRES_DB=perfmemory \
           --name pgvector \
           -p 5432:5432 \
           -d pgvector/pgvector:pg18
```

Here are the parameters used:

- `-e POSTGRES_USER=perfadmin` - Creates a database user
- `-e POSTGRES_PASSWORD=mypassword` - Sets the user's password
- `-e POSTGRES_DB=perfmemory` - Creates a new database
- `--name pgvector` - Names your container
- `-p 5432:5432` - Maps the container's PostgreSQL port to your host
- `-d` - Runs the container in detached mode

### Method 2: Run container using `docker-compose` (_recommended_)

First, create a `docker-compose.yaml` file with the following contents:

```yaml
services:
  pgvectordb:
    image: pgvector/pgvector:pg18
    container_name: pgvector
    ports:
      - "5432:5432"
    volumes:
      - ./data/pgvectordb:/var/lib/postgresql/18/docker
    environment:
      - POSTGRES_USER=perfadmin
      - POSTGRES_PASSWORD=mypassword
      - POSTGRES_DB=perfmemory
    restart: unless-stopped
```

Next, run the following command in the directory where your `docker-compose.yaml` file is located:

```bash
docker compose up -d
```

This command will pull the `pgvector/pgvector:pg18` image, start the Postgres container, and mount the data to `./data/pgvectordb/` for persistence.

### Container Lifecycle Commands

```bash
docker compose stop          # stop without removing
docker compose start         # restart a stopped container
docker compose down          # stop and remove container (data persists in ./data/)
docker compose down -v       # stop, remove container AND data (clean reset)
docker compose logs -f       # tail logs if something goes wrong
```

## Step 3: Installing the PSQL Client on Windows

You can install the PostgreSQL `psql` command-line client on Windows without the full server or with it, depending on your needs. Below are the steps for installing only the client or the full PostgreSQL package.

### Install Only the PSQL Client

1. Download the PostgreSQL installer package for Windows x86-64 from EnterpriseDB.
2. Double-click the downloaded installer and click Run when prompted.
3. Click Next on the installation wizard.
4. Choose the installation folder or keep the default, then click Next.
5. In the component selection screen, uncheck all except Command Line Tools.
6. Click Next and complete the installation process.
7. Open the Start menu, type psql, and select SQL Shell (psql) to run it.

Download from EnterpriseDB: https://www.enterprisedb.com/downloads/postgres-postgresql-downloads

## Step 4: Connect to PostgreSQL

### Method 1: Run PSQL Client Shell on Windows

Click on the Windows Start button and type `psql` in the search box. Soon you will see **SQL Shell (psql) client app** option. Select it to run.

### Method 2: Run PSQL Command via Command-Prompt

Open a command-prompt and you can connect to your newly created database using the psql command-line tool. You’ll be prompted for the password you set earlier.

```bash
psql -h localhost -U perfadmin -d perfmemory -p 5432
```

**Optional: Add PSQL to System Path**

1. Locate the bin folder in your PostgreSQL installation directory (e.g., `C:\Program Files\PostgreSQL\<version>\bin`).
2. Search for environment variables in Windows and open Edit the system environment variables.
3. Click Environment Variables, select Path under System variables, and click Edit.
4. Click New, add the bin folder path, and click OK.
5. Restart Command Prompt to apply changes.

## Step 5: Enabling pgvector

The pgvector Docker image ships with the extension pre-installed, but it must be enabled per database. Once connected to your database, enable it by running:

```sql
CREATE EXTENSION vector;
```

## Step 6: Verifying the Installation

To ensure pgvector is properly installed, run:

```sql
SELECT * FROM pg_extension;
```

You should see `vector` listed among the installed extensions.

## Step 7: Creating the PerfMemory Schema

Schema creation scripts are located in `perfmemory-mcp/sql/schema/`. Choose the script that matches your embedding provider:

| Provider | Script | Vector Dimensions |
|---|---|---|
| OpenAI / Azure OpenAI | `schema_openai.sql` | 1536 |
| Ollama (nomic-embed-text) | `schema_ollama.sql` | 768 |

> **Important:** Choose one provider and stick with it. Vectors from different embedding models cannot be mixed in the same database. See `docs/plans/pgvector-schema-design.md` for full details.

Run the script from the command line (example using the OpenAI schema):

```bash
psql -h localhost -U perfadmin -d perfmemory -f perfmemory-mcp/sql/schema/schema_openai.sql
```

Or if already connected via `psql`:

```sql
\i perfmemory-mcp/sql/schema/schema_openai.sql
```

### Verifying the Schema

After running the script, verify the tables were created:

```sql
\dt
```

You should see `debug_sessions` and `debug_attempts` listed. To verify the indexes:

```sql
\di
```