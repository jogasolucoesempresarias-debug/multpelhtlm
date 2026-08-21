"""Timeout do Agente — a guarda que protege o APP INTEIRO, não o chat.

⚠️ **Por que existe.** O cliente OpenAI era construído sem `timeout`, então valiam os defaults
do SDK: `read=600s` e `max_retries=2`, ou seja **1800s (30 min)** com uma thread do Waitress
presa. O `server.py` serve com `threads=8` — oito perguntas travadas param o painel inteiro,
Comercial junto: quem nunca abriu o chat também para.

Não é risco do recurso, é risco da instância. Por isso estes testes medem o COMPORTAMENTO real
contra um socket que aceita a conexão e nunca responde (o cenário do worker preso), em vez de
só conferir que o parâmetro está escrito.
"""
import socket
import threading
import time

import pytest

from estoque import ia


# ── um servidor que aceita e emudece ───────────────────────────────────────────────────────
class _BuracoNegro:
    """Aceita a conexão TCP e nunca envia byte nenhum. É pior que um servidor fora do ar: o
    `connect` tem sucesso, então só o read timeout salva."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._parar = threading.Event()
        self._presos = []
        self.t = threading.Thread(target=self._laco, daemon=True)
        self.t.start()

    def _laco(self):
        self.sock.settimeout(0.5)
        while not self._parar.is_set():
            try:
                c, _ = self.sock.accept()
                self._presos.append(c)          # segura aberto, sem responder
            except OSError:
                continue

    def fechar(self):
        self._parar.set()
        for c in self._presos:
            try:
                c.close()
            except OSError:
                pass
        self.sock.close()


@pytest.fixture
def buraco():
    b = _BuracoNegro()
    yield b
    b.fechar()


def test_conexao_MUDA_desiste_no_timeout_em_vez_de_prender_o_worker(buraco):
    """O teste que prova a correção: com timeout de 2s a chamada desiste em ~2s.

    Sem o parâmetro, esta mesma chamada seguraria a thread por 600s (e 1800s com os retries)."""
    from openai import OpenAI
    cli = OpenAI(api_key="sk-teste-nao-usada",
                 base_url=f"http://127.0.0.1:{buraco.port}/v1",
                 timeout=2.0, max_retries=0)
    t0 = time.time()
    with pytest.raises(Exception) as ei:
        stream = cli.chat.completions.create(
            model="gpt-4.1-mini", messages=[{"role": "user", "content": "oi"}],
            stream=True)
        for _ in stream:
            pass
    dt = time.time() - t0
    assert "timeout" in type(ei.value).__name__.lower() or "timeout" in str(ei.value).lower(), \
        f"esperava erro de timeout, veio {type(ei.value).__name__}: {ei.value}"
    # margem larga de propósito: o que se prova aqui é a ORDEM DE GRANDEZA (segundos, não
    # minutos), não a precisão do relógio
    assert dt < 15, f"desistiu em {dt:.1f}s — o timeout não está valendo"
    print(f"\n  desistiu em {dt:.2f}s (sem a correção: 600s, e 1800s com os retries do SDK)")


def test_o_pior_caso_do_DEFAULT_cabe_em_minutos_e_nao_em_meia_hora():
    """⚠️ O número que importa é `timeout x (1 + retries)`, não o timeout sozinho — foi assim que
    o default do SDK chegou a 30 min sem ninguém escolher isso.

    Trava o DEFAULT do módulo. Uma instância ainda pode piorar via env, mas aí é decisão de quem
    configurou, não herança silenciosa."""
    pior = ia.IA_TIMEOUT * (1 + ia.IA_MAX_RETRIES)
    assert pior <= 300, (f"pior caso de {pior}s por thread; com threads=8 no Waitress isso é o "
                         f"painel inteiro parado")
    print(f"\n  pior caso por thread: {ia.IA_TIMEOUT}s x (1+{ia.IA_MAX_RETRIES}) = {pior}s")


def test_a_rota_do_chat_CONSTROI_o_cliente_com_o_timeout():
    """Defesa contra a regressão por refactor: o teste de comportamento acima usa um cliente
    montado por ele mesmo, então passaria mesmo que a rota voltasse a construir o cliente sem
    timeout. Este amarra a rota real."""
    import inspect

    from estoque import routes

    fonte = inspect.getsource(routes.api_ia_chat)
    assert "OpenAI(" in fonte, "a rota tem de construir o cliente (âncora do teste)"
    # ⚠️ recorta a chamada BALANCEANDO parênteses: cortar no primeiro ")" pegaria o do
    # `os.getenv("OPENAI_API_KEY")` e o teste falharia com o código correto (aconteceu).
    resto = fonte[fonte.index("OpenAI("):]
    prof, fim = 0, len(resto)
    for i, ch in enumerate(resto):
        if ch == "(":
            prof += 1
        elif ch == ")":
            prof -= 1
            if prof == 0:
                fim = i + 1
                break
    bloco = resto[:fim]
    assert "timeout" in bloco, "o cliente da rota TEM de receber timeout explícito"
    assert "max_retries" in bloco, "sem limitar os retries o timeout é multiplicado pelo SDK"


# ── a outra metade da afirmação ────────────────────────────────────────────────────────────
class _StreamLento:
    """Servidor SSE que responde em 6 chunks com 1s de intervalo: duração TOTAL de ~6s, silêncio
    máximo de ~1s. Serve para separar as duas semânticas possíveis de `timeout`."""

    def __init__(self, n=6, intervalo=1.0):
        self.n, self.intervalo = n, intervalo
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self._parar = threading.Event()
        threading.Thread(target=self._laco, daemon=True).start()

    def _laco(self):
        self.sock.settimeout(0.5)
        while not self._parar.is_set():
            try:
                c, _ = self.sock.accept()
            except OSError:
                continue
            try:
                c.recv(65536)
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                          b"Cache-Control: no-cache\r\nConnection: close\r\n"
                          b"Transfer-Encoding: chunked\r\n\r\n")
                for i in range(self.n):
                    time.sleep(self.intervalo)
                    corpo = ('data: {"id":"x","object":"chat.completion.chunk","created":1,'
                             '"model":"gpt-4.1-mini","choices":[{"index":0,"delta":'
                             '{"content":"tok%d "},"finish_reason":null}]}\n\n' % i)
                    b = corpo.encode()
                    c.sendall(b"%x\r\n" % len(b) + b + b"\r\n")
                fim = b"data: [DONE]\n\n"
                c.sendall(b"%x\r\n" % len(fim) + fim + b"\r\n")
                c.sendall(b"0\r\n\r\n")
            except OSError:
                pass
            finally:
                try:
                    c.close()
                except OSError:
                    pass

    def fechar(self):
        self._parar.set()
        self.sock.close()


@pytest.fixture
def lento():
    s = _StreamLento()
    yield s
    s.fechar()


def test_o_timeout_conta_SILENCIO_entre_chunks_e_nao_a_duracao_total(lento):
    """⚠️ A correção só é segura se `timeout` for silêncio-entre-chunks. Se fosse duração TOTAL,
    pôr 60s introduziria um bug NOVO: toda resposta longa seria cortada no meio.

    Aqui a resposta demora ~6s no total com silêncio de ~1s, e o timeout é 3s. Se o critério
    fosse o total, isto estouraria; como é o silêncio, completa. É a prova de que `IA_TIMEOUT=60`
    não trunca resposta nenhuma — só mata conexão morta."""
    from openai import OpenAI
    cli = OpenAI(api_key="sk-teste-nao-usada",
                 base_url=f"http://127.0.0.1:{lento.port}/v1",
                 timeout=3.0, max_retries=0)
    t0 = time.time()
    toks = []
    stream = cli.chat.completions.create(
        model="gpt-4.1-mini", messages=[{"role": "user", "content": "oi"}], stream=True)
    for ch in stream:
        if ch.choices and ch.choices[0].delta.content:
            toks.append(ch.choices[0].delta.content)
    dt = time.time() - t0
    assert len(toks) == 6, f"recebeu {len(toks)} chunks, esperava 6 — o stream foi cortado"
    assert dt > 3.0, (f"a resposta inteira levou {dt:.1f}s, MENOS que o timeout de 3s — o teste "
                      f"não provou nada; aumente a duração do servidor lento")
    print(f"\n  stream de {dt:.1f}s completou inteiro sob timeout de 3s "
          f"({len(toks)} chunks, silêncio de ~1s entre eles)")
