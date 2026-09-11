# Omada → Technitium DNS

Serviço Python que consulta o inventário do TP-Link Omada e sincroniza registros nas zonas autoritativas do Technitium DNS Server. Inspirado no [coredns_omada](https://github.com/dougbw/coredns_omada), mas executado como serviço independente: não exige CoreDNS nem instalação de uma DNS App no Technitium.

```text
Omada Controller → consulta periódica → integração → API Technitium → registros DNS
```

Suporta clientes ativos, equipamentos, reservas DHCP habilitadas, múltiplos sites, registros A/AAAA/PTR e reservas com nomes como `*.apps`. O Technitium continua responsável por responder às consultas DNS. A sincronização é unidirecional e não altera o Omada.

## Início com Docker Compose

Você precisa de um controlador Omada acessível com usuário local e um Technitium já instalado com token de API.

```bash
cp config.example.json config.json
cp .env.example .env
chmod 600 .env
```

Edite `.env` com `OMADA_USERNAME`, `OMADA_PASSWORD` e `TECHNITIUM_TOKEN`. Edite `config.json` com os endereços dos servidores, o nome exato do site e os pares de sub-rede/domínio. No exemplo, `pc` com IP `192.168.1.10` torna-se `pc.home.arpa`.

Os campos `username_env`, `password_env` e `token_env` contêm os **nomes das variáveis**, nunca as credenciais. Mantenha os nomes do modelo e preencha os valores correspondentes somente no `.env`. O nome do site precisa corresponder ao exibido no Omada; `Default` é apenas um exemplo.

O usuário Omada precisa conseguir consultar clientes, redes e as fontes habilitadas. No Technitium, crie um token em **Administration → Sessions** com uma conta que tenha acesso às zonas necessárias. São necessárias permissões **Zones: View**, **Zone: View/Modify** e, se `create_zones` estiver habilitado, **Zones: Modify**. Para `prune`, conceda também **Zone: Delete**. A integração lê as zonas Primary visíveis para localizar os próprios registros; mantenha visíveis as zonas anteriormente gerenciadas para permitir sua limpeza.

```bash
docker compose build
docker compose run --rm omada-technitium --config /app/config.json --dry-run
```

A simulação consulta os dois servidores e imprime as operações planejadas, sem alterar registros ou salvar o estado de sincronização. Ela pode criar o diretório e arquivo de lock locais. Depois de revisar os nomes e IPs:

```bash
docker compose up -d
docker compose logs -f --tail=100
```

Não use `localhost` para apontar a outro servidor a partir do container: configure IP ou nome acessível pela rede Docker. O serviço não publica portas. Os clientes da rede devem usar o Technitium como DNS, por exemplo por meio da configuração DHCP do Omada.

Valide a resolução consultando diretamente o Technitium:

```bash
dig @192.168.1.3 pc.home.arpa A
dig @192.168.1.3 -x 192.168.1.10
```

## Configuração

### Arquivos locais e versionamento

Versione apenas os modelos `.env.example` e `config.example.json`, com valores fictícios, junto do `compose.yaml` compartilhado. O `.gitignore` exclui:

- `.env` e suas variantes, exceto `.env.example`;
- `config.json`, variantes `config.*.json` e cópias `config.json.*`, exceto `config.example.json`;
- overrides locais do Docker Compose, configurações de IDE, diretórios `secrets/` e `certs/`;
- estado em `data/`, logs e artefatos Python.

Esses arquivos permanecem disponíveis no computador, mas não entram em novos commits. A imagem Docker também usa uma lista explícita de arquivos permitidos no `.dockerignore`, mantendo credenciais e configurações locais fora do contexto de build.

Para conferir antes de um commit:

```bash
git check-ignore -v .env config.json
git ls-files -- .env config.json
git status --short
```

O segundo comando deve retornar vazio. Se esses arquivos já estiverem rastreados em outro checkout, retire-os apenas do índice com `git rm --cached -- .env config.json`. O `.gitignore` não remove conteúdo de commits anteriores.

### Opções

| Opção | Comportamento/padrão |
| --- | --- |
| `sites` | Lista explícita de sites pelo nome exato; obrigatório. |
| `sites[].networks` | Lista de `cidr` e `domain`; se omitida, descobre interfaces LAN com domínio DNS configurado no Omada. |
| `interval_seconds` | Intervalo entre ciclos; 300 segundos. |
| `ttl` | TTL dos registros gerenciados; 300 segundos. |
| `include_devices` | Inclui gateways, switches e APs; `true`. |
| `include_reservations` | Inclui reservas DHCP habilitadas, mesmo sem cliente conectado; `true`. |
| `reverse` | Cria PTR explícitos e gerenciados; `true`. |
| `create_zones` | Permite criar zonas Primary ausentes; padrão `false`, exemplo `true`. |
| `prune` | Remove registros próprios ausentes após a carência; padrão `false`. |
| `stale_seconds` | Carência desde a primeira ausência observada; 86400 segundos. |
| `owner` | Comentário que identifica a instância; `omada-technitium:default`. |
| `state_file` | Guarda os horários de ausência; exemplo `/data/state.json`. |
| `verify_tls` | Valida certificados; `true`, independente para cada servidor. |
| `timeout_seconds` | Timeout HTTP; 30 segundos por requisição. |

Para múltiplos sites/VLANs, por exemplo:

```json
"sites": [
  {
    "name": "Matriz",
    "networks": [
      {"cidr": "192.168.10.0/24", "domain": "home.arpa"},
      {"cidr": "192.168.20.0/24", "domain": "iot.home.arpa"},
      {"cidr": "fd00:1234::/64", "domain": "home.arpa"}
    ]
  },
  {"name": "Filial"}
]
```

No exemplo, a Filial usa os domínios das suas interfaces LAN. Redes sem domínio não são sincronizadas na descoberta automática. Redes e clientes são associados dentro de cada site, usando o prefixo mais específico. Sites com sub-redes sobrepostas devem usar domínios distintos; conflitos globais de nomes interrompem o ciclo.

## Nomes, atualizações e remoção

- O nome configurado no Omada tem prioridade. Quando vazio ou igual ao MAC, usa hostname do cliente ou descrição da reserva; o MAC é a última alternativa. Acentos são transliterados e caracteres inválidos viram hífens. Nomes são tratados como um rótulo sob o domínio configurado.
- Reservas habilitadas têm prioridade sobre clientes com o mesmo MAC. Endereços IPv6 de `ipv6List` são incluídos somente quando pertencem a uma rede IPv6 configurada/descoberta. Endereços inválidos, link-local, loopback e multicast são ignorados.
- Cada registro recebe o comentário exato de `owner`. Registros manuais e de outras instâncias nunca são adotados ou sobrescritos. Um conflito interrompe o planejamento completo; renomeie o dispositivo ou resolva o conflito no DNS.
- Um registro próprio com valor único muda de IP via `records/update`, mesmo com `prune: false`. Conjuntos com múltiplos valores usam adição e limpeza por carência. TTL e estado habilitado também são reconciliados.
- Com `prune: false`, registros antigos de dispositivos renomeados/removidos e PTRs de IPs anteriores permanecem. Ative `prune: true` para limpá-los após `stale_seconds`. A limpeza inclui registros próprios de redes removidas e fontes posteriormente desabilitadas.
- O estado persiste no volume Docker. Perder esse arquivo reinicia a carência; não causa exclusão imediata. Mantenha o `owner` estável e exclusivo por instância. Execute somente uma instância por `owner`; o lock impede concorrência apenas quando o arquivo de estado é compartilhado.
- Uma leitura incompleta ou erro do Omada/Technitium interrompe o ciclo antes das escritas. Todas as leituras e conflitos são verificados antes da aplicação. As APIs não fornecem transação entre zonas: uma falha de escrita pode deixar alterações parciais, reconciliadas no próximo ciclo. Não há reversão automática.
- PTRs usam a zona reversa existente mais específica; quando ausente, propõem zonas IPv4 `/24` e IPv6 `/64`. Revise esses limites na simulação ou crie previamente a zona desejada. Não há suporte automático a delegação reversa classless RFC 2317. Wildcards não geram PTR; havendo aliases, o primeiro nome em ordem lexicográfica é o canônico.
- Zonas existentes devem ser Primary, habilitadas e não internas. A integração nunca exclui zonas. Evite alterações concorrentes nos mesmos registros entre o planejamento e a aplicação.

## TLS e autenticação

Use usuário local Omada, sem fluxo interativo de MFA/SSO. A integração utiliza cookies e CSRF e abre nova sessão a cada ciclo. Se a sessão expirar durante uma leitura, o ciclo falha e o seguinte autentica novamente. Não é um cliente OAuth da Open API pública de aplicações.

Prefira HTTPS e certificados confiáveis. Para CA privada, monte o certificado no container e configure `SSL_CERT_FILE` com o caminho para um bundle confiável. Para um controlador local com certificado autoassinado, é possível definir `omada.verify_tls: false` explicitamente. O exemplo Technitium usa a porta HTTP 5380; ajuste para seu endpoint HTTPS se configurado. Senhas e tokens ficam em variáveis de ambiente e não aparecem nos logs da integração; no Technitium, o token é enviado no corpo POST, não na URL.

## Execução sem Docker

Python 3.11+ em Linux, somente biblioteca padrão, sem dependências pip. Copie a configuração e altere `state_file` para `data/state.json`. Exporte as três variáveis de ambiente no shell; o programa não carrega `.env` automaticamente fora do Compose.

```bash
python3 -m omada_technitium --config config.json --dry-run
python3 -m omada_technitium --config config.json --once
python3 -m omada_technitium --config config.json
```

`--once` e `--dry-run` retornam código 1 em caso de falha. O modo contínuo registra a falha e tenta novamente após o intervalo. SIGTERM/SIGINT interrompem a espera; uma requisição em andamento pode levar até o timeout para terminar.

## Testes e compatibilidade

```bash
python3 -m unittest discover -s tests -v
```

A suíte cobre os fluxos Omada legado e 6.2+, paginação, geração de registros, isolamento entre sites, reservas, conflitos, preservação de registros manuais, carência, simulação, falhas parciais e HTTP real em localhost com cookies/CSRF e bloqueio de redirecionamento. Nenhum teste acessa um controlador ou DNS de produção.

Os endpoints foram conferidos no cliente [go-omada v0.7.0](https://github.com/dougbw/go-omada/tree/v0.7.0), usado pelo projeto de referência: GET legado `/{controllerId}/api/v2/sites/{siteId}/clients` e, a partir de 6.2.0, POST `/openapi/v2/{controllerId}/sites/{siteId}/clients` com `Omada-Request-Source: web-local`. As demais fontes seguem os endpoints do mesmo cliente.

Os 22 testes automatizados passaram localmente e na imagem Docker. Em uma simulação com Omada 6.2.14 e Technitium reais, foram validadas autenticação nos dois serviços, leitura do inventário Omada e listagem de zonas DNS. A simulação detectou reservas com nomes duplicados e interrompeu o planejamento antes de qualquer escrita. A aplicação de registros no ambiente real ainda não foi validada. Variações de firmware e controladores Cloud podem exigir adaptações.

Referências: [coredns_omada](https://github.com/dougbw/coredns_omada), [go-omada](https://github.com/dougbw/go-omada/tree/v0.7.0), [API oficial do Technitium](https://github.com/TechnitiumSoftware/DnsServer/blob/master/APIDOCS.md). Esta implementação Python foi escrita para este projeto; os projetos de referência foram consultados para comportamento e contratos de API.

## CI/CD e Docker Hub

O workflow [CI / Docker Hub](.github/workflows/ci.yml) testa o código em Python 3.11 e 3.13, constrói a imagem e repete os testes no container. Somente após essas etapas publica em [newtcv/omada-technitiumdns](https://hub.docker.com/r/newtcv/omada-technitiumdns), para `linux/amd64` e `linux/arm64`. Os testes de execução usam amd64; a imagem arm64 é construída via QEMU. A publicação inclui metadados OCI, proveniência mínima, SBOM e digest no resumo do GitHub Actions.

### Configurar a publicação

1. No Docker Hub, crie um Personal Access Token com permissão **Read & Write** para uma conta que possa publicar em `newtcv/omada-technitiumdns`.
2. No repositório GitHub, abra **Settings → Secrets and variables → Actions** e crie o **repository secret** `DOCKERHUB_TOKEN` com esse token.
3. O usuário Docker Hub padrão é `newtcv`. Se o token pertencer a outro usuário com acesso ao mesmo repositório, defina a **repository variable** `DOCKERHUB_USERNAME`.
4. Envie o workflow para a branch `main`. O push dispara os testes e a publicação. Também é possível usar **Actions → CI / Docker Hub → Run workflow**, selecionando `main` ou uma tag `v*`.

As credenciais de Omada/Technitium não são necessárias no GitHub Actions. Não envie o `.env` ou o `config.json` local para o GitHub nem para a imagem; os testes usam apenas dados simulados e a configuração de exemplo.

| Evento | Resultado |
| --- | --- |
| Pull request para `main`, inclusive de forks | Testes e build do container, sem login ou publicação. |
| Push ou execução manual em `main` | Publica `latest` e `sha-<SHA completo>`. |
| Push de tag `v1.2.3` | Publica `v1.2.3`, `1.2.3`, `1.2` e `sha-<SHA completo>`. |
| Tag de pré-lançamento, como `v1.2.3-rc.1` | Publica a tag original, a versão de pré-lançamento e o SHA; não altera `latest`. |
| Execução manual em outra branch | Testes e build, sem publicação. |

`latest` acompanha `main`; tags de versão não alteram `latest`. Para fixar uma versão em produção, use a tag completa ou o digest da imagem. A publicação é habilitada somente no repositório `newtcv/omada-technitiumdns`.

Para publicar uma versão a partir do commit desejado:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Após a primeira publicação bem-sucedida, substitua `build: .` no seu Compose por:

```yaml
image: newtcv/omada-technitiumdns:latest
```

Mantenha `env_file`, volumes e as demais opções do serviço. Então execute:

```bash
docker compose pull
docker compose up -d
```

O pipeline segue as ações oficiais de [build e publicação](https://docs.docker.com/build/ci/github-actions/push-multi-registries/) e [gerenciamento de tags](https://docs.docker.com/build/ci/github-actions/manage-tags-labels/) do Docker.
