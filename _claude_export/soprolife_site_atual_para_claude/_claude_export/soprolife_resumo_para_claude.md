# Briefing para melhorar o aspecto geral do site da Sopro Life

## Contexto geral

A Sopro Life é uma empresa/serviço de saúde respiratória no Rio de Janeiro, com foco em espirometria, prova de função pulmonar, atendimento respiratório, parcerias com clínicas/médicos/empresas e, mais recentemente, telemedicina respiratória.

O site é estático, em HTML/CSS/JS, publicado via GitHub Pages no domínio soprolife.com.br.

A identidade visual já aprovada usa:

- azul navy;
- teal / verde-água;
- branco e azul muito claro;
- visual médico limpo, premium e confiável;
- WhatsApp como principal canal de conversão;
- Instagram no cabeçalho;
- layout com cards arredondados, sombras suaves e aparência moderna.

## Páginas principais

As páginas ativas principais são:

- `/` — home;
- `/conheca/` — página institucional;
- `/servicos/` — serviços;
- `/espirometria/` — página do exame;
- `/espirometria-rio-de-janeiro/` — página importante para SEO local;
- `/telemedicina/` — nova página de telemedicina respiratória;
- `sitemap.xml`;
- assets em `/img/`.

## Funcionalidades que não podem quebrar

Preservar obrigatoriamente:

1. Menu superior com:
   - Início
   - Conheça a Sopro
   - Serviços
   - Agendamento
   - Telemedicina
   - Contato

2. Botão principal:
   - “Agendar pelo WhatsApp”

3. Agenda de espirometria:
   - bloco de agendamento rápido;
   - tipo de espirometria;
   - localidade;
   - data;
   - horários;
   - horários clicáveis abrindo WhatsApp;
   - localidade domiciliar como principal;
   - mapa com botão “Ampliar mapa”.

4. Página `/espirometria-rio-de-janeiro/`:
   - importante para busca do Google;
   - manter SEO local;
   - manter agenda e mapa com comportamento semelhante à home.

5. Página `/telemedicina/`:
   - deve mostrar duas médicas:
     - Dra. Carla Ferreira;
     - Dra. Ana Cristina do Nascimento Cunha.
   - Dra. Carla possui foto em `/img/dra-carla-ferreira.jpg`;
   - Dra. Carla tem horários;
   - Dra. Ana deve aparecer sem horários disponíveis;
   - texto deve falar em consulta online, não atendimento domiciliar.

6. Favicon, logo e imagem institucional:
   - não inventar nova logo;
   - manter logo da Sopro Life;
   - não trocar identidade visual.

## Pedido de melhoria

Melhorar o aspecto visual geral do site, com inspiração em grandes sites de clínicas e hospitais, mas mantendo a identidade própria da Sopro Life.

Prioridades:

1. Melhorar a home:
   - dar mais destaque à telemedicina;
   - integrar melhor espirometria + teleconsulta;
   - substituir blocos repetitivos ou muito simples por cards mais bonitos;
   - deixar o hero mais profissional;
   - tornar os cards mais coerentes entre si.

2. Melhorar a página de telemedicina:
   - layout mais bonito para as duas médicas;
   - boa visualização em desktop e Android;
   - manter foto da Dra. Carla;
   - deixar horários e disponibilidade mais claros.

3. Melhorar responsividade:
   - no celular, os elementos importantes devem aparecer cedo;
   - evitar que o usuário precise rolar muito para ver agenda, telemedicina ou CTAs;
   - menu deve funcionar bem em Android.

4. Melhorar consistência visual:
   - padronizar espaçamentos;
   - padronizar sombras e bordas;
   - padronizar botões;
   - manter navy/teal;
   - evitar excesso de elementos.

5. Não quebrar:
   - WhatsApp;
   - agenda;
   - mapa;
   - menu;
   - SEO;
   - links internos;
   - sitemap.

## Observação importante

O dono do site prefere trabalhar por terminal e patches diretos. Então o ideal é devolver alterações em blocos claros de HTML/CSS/JS ou comandos aplicáveis, sem depender de editor visual.
