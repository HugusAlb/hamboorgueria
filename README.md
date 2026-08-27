# 🎃 Hamboorgueria

App web para gestão de uma hamburgueria: pedidos do cliente, caixa, cozinha e auditoria, tudo em tempo real via HTMX + SSE.

## Stack

- **Backend**: FastAPI + SQLModel + SQLite (WAL) + Jinja2
- **Frontend**: HTMX + Tailwind CSS (via CDN, sem build step)
- **Testes**: pytest + httpx (`TestClient`), banco SQLite in-memory
- **Deps**: Poetry (`pyproject.toml`)

## Funcionalidades

- **Cliente**: cadastro/login por telefone, cardápio, pedido (mesa ou retirada), acompanhamento do status em tempo real
- **Caixa**: confirmação de pedidos pendentes, registro manual de pedido, cobrança, controle de caixa (entradas/saídas)
- **Cozinha**: painel kanban dos pedidos aceitos, atualização de status (preparando → pronto → entregue)
- **Auditoria**: visão consolidada de caixa, produtos e solicitações de edição de pedido
- Modo claro/escuro persistido em `localStorage`
- Rotas `/demo/*` com todas as telas lado a lado, sem necessidade de login (quando `DEMO_ENABLED=true`)

## Como rodar localmente

```bash
poetry install
poetry run uvicorn app.main:app --reload
```

A aplicação sobe em `http://localhost:8000`. O banco `hamboorgueria.db` (SQLite) é criado e migrado automaticamente na primeira execução.

## Variáveis de ambiente (`.env`)

| Variável | Descrição |
|---|---|
| `HTTPS` | `true`/`false` — controla a flag `secure` dos cookies de sessão |
| `DEMO_ENABLED` | `true`/`false` — habilita as rotas `/demo/*` sem autenticação |

## Usuários padrão (seed)

| Perfil | Login | Senha |
|---|---|---|
| Caixa | `caixa` | `caixa123` |
| Cozinha | `cozinha` | `cozinha123` |
| Auditoria | `auditoria` | `adm123` |

Cliente demo: telefone `00000000000`, senha `demo123`.

## Testes

```bash
poetry run pytest              # todos os testes
poetry run pytest tests/test_caixa.py -v   # um arquivo específico
```

## Deploy

Configurado para [Square Cloud](https://squarecloud.app) via `squarecloud.app` + `requirements.txt` (o build da Square Cloud não usa Poetry). As variáveis de ambiente (`HTTPS`, `DEMO_ENABLED`) precisam ser configuradas manualmente no painel, já que o `.env` não é versionado.
