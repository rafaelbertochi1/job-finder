# Job Finder — vagas de TI no Brasil

Entende o seu perfil (currículo + entrevista curta), busca vagas em várias fontes, dá uma nota de
aderência para cada uma com o Claude e se candidata: de forma **assistida** (você confirma) ou
**automática** onde isso é permitido.

```
currículo + entrevista ──► perfil (data/profile.json)
                                 │
  Gupy · Remotar · GitHub (backend-br, frontendbr...) · Greenhouse/Lever · busca na web
                                 │
                 pré-filtro (modalidade, cidade, palavras-chave)  ← grátis
                                 │
              Claude: nota 0-100, skills que batem/faltam, regime, como se candidatar
                                 │
          relatório HTML  +  fila de candidaturas (nota ≥ min_score_apply)
                                 │
     e-mail com CV · formulário preenchido pela IA · plataformas com login (assistido)
```

## Instalação (uma vez)

```bash
pip install -r requirements.txt
```

```bash
python -m playwright install chromium
```

1. Acesso ao Claude, uma das duas opções:
   - **Assinatura do Claude (sem chave):** instale o Claude Code e faça login. O programa usa o
     comando `claude` e consome o limite de uso do seu plano.
     ```bash
     irm https://claude.ai/install.ps1 | iex
     ```
     ```bash
     claude auth login
     ```
   - **API:** coloque sua `ANTHROPIC_API_KEY` no `.env` (cobrança por uso). Se a chave existir, ela tem prioridade.

   Copie `.env.example` para `.env` (opcional: `GITHUB_TOKEN` e SMTP para candidaturas por e-mail).
2. Coloque seu currículo em `data/curriculo.pdf` (ou ajuste `cv_path` no `config.yaml`).

## Uso

```bash
python -m jobfinder setup
```
Lê o currículo, mostra o que entendeu e faz de 6 a 12 perguntas (pretensão, modalidade, cidades,
CLT/PJ, inglês, o que você não aceita...). Enter aceita a sugestão. O resultado fica em
`data/profile.json`, que você pode editar à mão.

```bash
python -m jobfinder search
```
Busca vagas novas, filtra e dá nota. Vagas já vistas não são analisadas de novo.
Use `--no-web` para pular a busca na web (mais barata).

```bash
python -m jobfinder import --file vagas.jsonl --score
```
Importa vagas de um arquivo (uma vaga JSON por linha, ou `--clipboard` para ler da área de
transferência) e já filtra e dá nota.

```bash
python -m jobfinder report --open
```
Relatório em `output/relatorio.html`, com filtros por modalidade e status.

```bash
python -m jobfinder apply
```
Candidata-se às melhores vagas da fila. Opções:
- `--mode assisted` ou `--mode auto` (padrão: `apply.mode` do config)
- `--job <ID>` para uma vaga específica (o ID aparece no relatório)
- `--limit N` e `--dry-run` (gera materiais e preenche formulários, mas não envia nada)

```bash
python -m jobfinder list --status applied
```

## Como a candidatura funciona

Para cada vaga o Claude gera **carta de apresentação, e-mail e pitch curto** usando só fatos do seu
perfil (ficam salvos em `output/applications/<id>/materiais.md`). Depois:

| Canal | Quando | Assistido | Automático |
|---|---|---|---|
| E-mail | A vaga pede "envie seu CV para..." | Mostra o e-mail, você confirma | Envia com o CV anexo (precisa de SMTP no `.env`) |
| Formulário (ATS) | Greenhouse, Lever, Ashby, Workable, sites próprios | Abre o navegador, `[f]` preenche com IA, você revisa e envia | Só nos domínios de `auto_submit_domains`, sem captcha e sem campo que dependa de você |
| Plataforma | Gupy, LinkedIn, Indeed, Catho... | Abre a vaga logado (seu login fica salvo), `[f]` ajuda a preencher, **você** clica em enviar | Nunca — os termos desses sites proíbem automação e a conta pode ser bloqueada |

No modo automático, o que não puder ser enviado sozinho fica na fila e, se você estiver no terminal,
o programa oferece abrir o modo assistido logo em seguida.

A IA nunca inventa dados: CPF, data de nascimento, aceite de termos e perguntas pessoais ficam
sempre para você.

## Rodar todo dia (Agendador de Tarefas do Windows)

Com `apply.mode: auto` no config, o comando abaixo busca, gera o relatório e envia o que for
permitido. O que precisar de você espera na fila.

```bash
schtasks /Create /SC DAILY /ST 08:30 /TN "JobFinder" /TR "cmd /c cd /d %USERPROFILE%\Documents\Job_Finder && python -m jobfinder run >> output\run.log 2>&1"
```

## Custos

Busca nas fontes é grátis. O Claude é usado no perfil (uma vez), na nota de cada vaga **nova**
que passou no pré-filtro, nos materiais de cada candidatura e na busca na web.
Na primeira rodada podem ser 100-300 vagas; nas seguintes, só as novas. Para gastar menos:
`--no-web`, `max_age_days` menor, ou `model` mais barato no `config.yaml`.

## Ajustes úteis (`config.yaml`)

- `matching.min_score_apply`: nota mínima para entrar na fila (padrão 70).
- `search.sources.github.repos`: repositórios de vagas da comunidade.
- `search.sources.greenhouse.boards` / `lever.companies`: empresas que você quer monitorar.
- `apply.auto_submit_domains` / `assisted_only_domains`: onde o envio automático é permitido.
