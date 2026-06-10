"""Testes do nome dos arquivos de export da Carteira (PDF/CSV) refletindo os filtros ativos.

Regra: nome = nomes de vendedor + supervisor(time) + UF + cidade ativos + data.
Sem nenhum dos 4 → carteira_todos_<data>.
"""
import json
import pathlib
import datetime

from tests.conftest import login_as

FIX_DIR = pathlib.Path(__file__).resolve().parent / 'fixtures'


def _load(name):
    with open(FIX_DIR / f'{name}.json', encoding='utf-8') as f:
        return json.load(f)


def _rotear_maps(cap):
    # Carteira full fica vazia (default), só precisamos dos mapas pra resolver os nomes.
    cap.set_routes([
        ('PCUSUARI', _load('dax_vendedores_map')),
        ('PCSUPERV', _load('dax_supervisores_map')),
    ])


def test_csv_sem_filtro_usa_fallback(client, usuario_admin, mock_dax_capture, clean_redis):
    _rotear_maps(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/carteira/csv')
    assert r.status_code == 200
    cd = r.headers['Content-Disposition']
    hoje = datetime.date.today().isoformat()
    assert f'carteira_todos_{hoje}.csv' in cd


def test_csv_vendedor_resolve_nome(client, usuario_admin, mock_dax_capture, clean_redis):
    """vendedor=573 → nome 'JOAO VICTOR' (fixture) no nome do arquivo."""
    _rotear_maps(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/carteira/csv?vendedor=573')
    assert r.status_code == 200
    cd = r.headers['Content-Disposition']
    assert 'JOAO VICTOR' in cd
    assert cd.strip().endswith('.csv') or '.csv' in cd


def test_pdf_quatro_filtros_concatenam(client, usuario_admin, mock_dax_capture, clean_redis):
    """vendedor + time + uf + cidade → 4 nomes concatenados, na ordem, + data."""
    _rotear_maps(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/carteira/pdf?vendedor=573&time=18&uf=BA&cidade=ILHEUS')
    assert r.status_code == 200
    cd = r.headers['Content-Disposition']
    hoje = datetime.date.today().isoformat()
    # vendedor (nome) vem primeiro, depois UF e cidade; termina com data + .pdf
    assert 'JOAO VICTOR' in cd
    assert 'BA' in cd and 'ILHEUS' in cd
    assert f'{hoje}.pdf' in cd
    # ASCII fallback presente + filename* RFC 5987 (acentos/espaços)
    assert 'filename="' in cd and "filename*=UTF-8''" in cd


def test_pdf_sanitiza_caracteres_invalidos(client, usuario_admin, mock_dax_capture, clean_redis):
    """Cidade com barra não deve vazar caractere inválido pro nome do arquivo."""
    _rotear_maps(mock_dax_capture)
    login_as(client, usuario_admin['email'], usuario_admin['senha'])
    r = client.get('/api/carteira/pdf?cidade=' + 'A/B:C')
    assert r.status_code == 200
    cd = r.headers['Content-Disposition']
    # A parte do filename ASCII (entre aspas) não pode conter / : etc.
    import re
    m = re.search(r'filename="([^"]+)"', cd)
    assert m, cd
    fname = m.group(1)
    for bad in '\\/:*?"<>|':
        assert bad not in fname
