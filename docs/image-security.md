# Análise dos alertas da imagem

Consulta às fontes em 11/09/2026. Esta análise não substitui uma varredura do digest publicado nem declara a imagem livre de vulnerabilidades.

## Componentes Python

A integração não usa pacotes externos. A inspeção da imagem local anterior encontrou `pip 26.2.1`, `pip/_vendor/msgpack` e o wheel do pip dentro de `ensurepip`. Não encontrou uma instalação independente de setuptools; sem o digest e os caminhos do relatório original, não é possível atribuir com certeza o alerta de setuptools à mesma imagem inspecionada.

O Dockerfile agora remove as ferramentas Python de instalação e o wheel de recuperação do ensurepip. Assim, a imagem de execução não precisa carregar msgpack ou setuptools, nem suas versões corrigidas.

| Alertas reportados | Tratamento |
| --- | --- |
| CVE-2025-47273, CVE-2026-59890 — setuptools | Remoção das ferramentas de instalação; confirmar ausência no inventário do novo digest. |
| CVE-2026-57585, GHSA-6v7p-g79w-8964 — msgpack | São identificadores do mesmo problema. Remover o pip também elimina sua cópia vendorizada de msgpack. |

Para aplicações que precisem manter esses pacotes, o [aviso do msgpack](https://github.com/msgpack/msgpack-python/security/advisories/GHSA-6v7p-g79w-8964) informa correção em 1.2.1; o [aviso do setuptools para CVE-2026-59890](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c) informa correção em 83.0.0. A abordagem deste projeto é remover ferramentas desnecessárias, não instalar dependências adicionais na aplicação.

## Pacotes Debian que permanecem

O build preserva os pacotes e metadados do sistema; não força a remoção de pacotes essenciais nem oculta os registros usados pelos scanners.

| Alerta | Situação consultada / consequência |
| --- | --- |
| [CVE-2026-85091 — zlib](https://security-tracker.debian.org/tracker/CVE-2026-85091) | O tracker Debian marca a versão do Trixie como vulnerável e sem versão corrigida. O upgrade APT não resolve enquanto não houver pacote corrigido nos repositórios configurados. |
| [CVE-2025-45582 — tar](https://security-tracker.debian.org/tracker/CVE-2025-45582) | O Debian registra o problema como contestado pelo upstream. A aplicação não executa tar nem extrai arquivos TAR recebidos. Isso limita a exposição neste serviço, mas não equivale a corrigir o pacote. |
| [CVE-2005-2541 — tar](https://security-tracker.debian.org/tracker/CVE-2005-2541) | Classificado como `unimportant`, relacionado a comportamento intencional de extração de permissões. O pacote permanece na base. |
| [CVE-2019-1010024](https://security-tracker.debian.org/tracker/CVE-2019-1010024), [CVE-2019-1010023](https://security-tracker.debian.org/tracker/CVE-2019-1010023) — glibc | Classificados como `unimportant`; as notas registram que o upstream não os trata como problemas de segurança. Não há correção aplicada por este projeto. |
| [CVE-2019-9192 — glibc](https://security-tracker.debian.org/tracker/CVE-2019-9192) | Classificado como `unimportant` e contestado pelo mantenedor. A glibc continua necessária à base Debian/Python. |

O container executa com usuário sem privilégios; o Compose e os testes do CI retiram capabilities e habilitam `no-new-privileges`. Essas medidas reduzem a exposição, mas não devem ser descritas como correções das bibliotecas.

## Resultado da verificação local

Após as alterações, o build `--pull --no-cache` concluiu e os 22 testes passaram dentro do container. A inspeção do sistema de arquivos confirmou ausência de pip, ensurepip, setuptools, wheel, msgpack, cópias vendorizadas de msgpack/setuptools e arquivos `.whl` sob `/usr/local/lib/python3.13`.

Uma varredura local com Trivy 0.74.0 em 11/09/2026 analisou a imagem amd64 de ID `sha256:a0dd62da2dd075d45f277319aa73bf54579353197c11971bd7c0389d1fe2013b`, exportada com `docker save`. Não houve resultados de pacotes Python. Para os 87 pacotes Debian inventariados, o scanner retornou **178 ocorrências por pacote/CVE**: 3 críticas, 51 altas, 57 médias, 57 baixas e 10 de severidade desconhecida. Nenhuma ocorrência trouxe `FixedVersion` na base consultada. As críticas foram atribuídas ao pacote `perl-base`, não ao código Python da integração.

Essas contagens incluem a mesma CVE associada a diferentes pacotes binários e não são diretamente comparáveis às do relatório original sem o mesmo digest, scanner e banco de dados. O Trivy também emitiu aviso sobre SBOM de terceiros, portanto o resultado requer a avaliação usual de aplicabilidade; não representa prova de exploração. A imagem **ainda contém alertas de segurança do Debian**, além dos originalmente informados, e não foi publicada nesta verificação.

## Validação após publicar

1. Use o digest informado no resumo da publicação do GitHub Actions.
2. Analise cada arquitetura utilizada, pois amd64 e arm64 têm manifestos e pacotes próprios.
3. Confirme a ausência de pip, ensurepip, setuptools e msgpack no sistema de arquivos final.
4. Confira novamente os alertas Debian e as versões disponíveis. Não adicione exceções globais ao scanner apenas para obter um relatório sem alertas.
