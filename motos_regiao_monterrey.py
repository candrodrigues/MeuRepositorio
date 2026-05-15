import os
import requests
import time
from datetime import datetime


# ================== CONFIGURAÇÕES ==================

TOKEN_MOTTU = os.environ.get("TOKEN_MOTTU", "seu_token_mottu_aqui")

PIPEFY_TOKEN = os.environ.get("PIPEFY_TOKEN", "")
PIPEFY_PIPE_ID     = 303177520
PIPEFY_RESPONSAVEL = "Caio Andrade Rodrigues"

REGION_ID = "7d5f8432-ad96-4684-90f0-0ae08b4b8012"

# Ajuste para testes — use None para rodar todas as placas
LIMITE_PLACAS  = 10
PAUSA_SEGUNDOS = 1

# ==================================================

data_execucao = datetime.today().strftime("%Y-%m-%d %H:%M:%S")

total_placas_consultadas = 0
total_multas_encontradas = 0
total_cards_criados      = 0
total_erros              = 0


# ================== API MOTTU ==================

def get_branches(token: str) -> list[dict]:
    url = f"https://branch-management.mottu.cloud/branches?regionIds={REGION_ID}&active=true"
    headers = {
        "accept": "text/plain",
        "Authorization": f"Bearer {token}",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()["result"]


def get_vehicles(token: str, branch_ids: list[str]) -> list[dict]:
    url = "https://vehicle.mottu.cloud/api/v3/Vehicle/GetVehicles"
    headers = {
        "accept": "text/plain",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    r = requests.post(url, headers=headers, json={"branchIds": branch_ids}, timeout=30)
    r.raise_for_status()
    return r.json()["result"]


# ================== CRAWLER MULTAS MONTERREY ==================

HEADERS_MULTAS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Connection": "keep-alive",
    "Referer": "https://asp.monterrey.gob.mx/multasdetransito/",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Mobile Safari/537.36 Edg/147.0.0.0"
    ),
    "sec-ch-ua": '"Microsoft Edge";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

COOKIES_MULTAS = {
    "cookiesession1": "678B288E851FB7345448CE8EE9CD93DD",
}


def get_fines(plate: str, session: requests.Session) -> list[dict]:
    """Consulta multas pendentes de uma placa no site de Monterrey."""
    url = f"https://asp.monterrey.gob.mx/apitransito/fines/search?placa={plate}"
    try:
        r = session.get(url, headers=HEADERS_MULTAS, cookies=COOKIES_MULTAS, timeout=15)
        r.raise_for_status()
        data = r.json()

        if isinstance(data, dict) and "fines" in data:
            return data["fines"]
        if isinstance(data, list):
            return data
        return []

    except requests.exceptions.RequestException as e:
        print(f"  [✖] Erro ao consultar placa {plate}: {e}")
        return []


# ================== PIPEFY ==================

def formatar_data(data_iso: str) -> str:
    """Converte data ISO 8601 para formato aceito pelo Pipefy (YYYY-MM-DD)."""
    if not data_iso:
        return ""
    try:
        return data_iso[:10]
    except Exception:
        return ""


def calcular_total_a_pagar(monto, descuento) -> str:
    """Calcula o total a pagar: monto - descuento."""
    try:
        total = float(monto or 0) - float(descuento or 0)
        return str(round(total, 2))
    except (ValueError, TypeError):
        return str(monto or "")


def create_pipefy_card(fine: dict) -> dict:
    """Cria um card no Pipefy com todos os dados da multa."""
    global total_cards_criados, total_erros

    placa            = fine.get("placa", "")
    boleta           = str(fine.get("boleta", ""))
    data             = formatar_data(fine.get("fecha_infraccion", ""))
    monto            = fine.get("monto", 0)
    descuento        = fine.get("descuento", 0)
    total_a_pagar    = calcular_total_a_pagar(monto, descuento)
    orden_de_pago    = str(fine.get("orden_de_pago", boleta))
    lugar_infraccion = fine.get("lugar_infraccion", "").replace('"', "'")
    link_monterrey   = f"https://asp.monterrey.gob.mx/multasdetransito/?placa={placa}"

    title = f"Automación - Multa {placa}"

    # Mapeamento completo com os field_ids reais do pipe
    fields_attributes = [
        # Campos dinâmicos (vindos do endpoint de Monterrey)
        {"field_id": "cu_l_es_tu_nombre", "field_value": PIPEFY_RESPONSAVEL},
        {"field_id": "placa",             "field_value": placa},
        {"field_id": "fecha_infracci_n",  "field_value": data},
        {"field_id": "total_a_pagar",     "field_value": total_a_pagar},
        {"field_id": "infracci_n",        "field_value": orden_de_pago},
        {"field_id": "nota",              "field_value": lugar_infraccion},
        {"field_id": "link_para_hacer_el_pago", "field_value": link_monterrey},
        # Campos fixos (chumbados)
        {"field_id": "regi_n",            "field_value": "Monterrey"},
        {"field_id": "pago_ya_realizado", "field_value": "No"},
        {"field_id": "tipo",              "field_value": "Multa"},
    ]

    fields_str = ", ".join(
        '{field_id: "%s", field_value: "%s"}' % (f["field_id"], f["field_value"])
        for f in fields_attributes
    )

    mutation = """
    mutation {
      createCard(input: {
        pipe_id: %d
        title: "%s"
        fields_attributes: [%s]
      }) {
        card {
          id
          title
          url
        }
      }
    }
    """ % (PIPEFY_PIPE_ID, title, fields_str)

    try:
        r = requests.post(
            "https://api.pipefy.com/graphql",
            headers={
                "Authorization": f"Bearer {PIPEFY_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"query": mutation},
            timeout=15,
        )
        r.raise_for_status()
        data_resp = r.json()

        if "errors" in data_resp:
            print(f"  [✖] Erro GraphQL ({placa}): {data_resp['errors']}")
            total_erros += 1
            return {}

        card = data_resp["data"]["createCard"]["card"]
        total_cards_criados += 1
        return card

    except requests.exceptions.RequestException as e:
        print(f"  [✖] Falha ao criar card Pipefy ({placa}): {e}")
        total_erros += 1
        return {}


# ================== EXECUÇÃO ==================

print("\n==============================")
print("INICIO DA EXECUÇÃO")
print(f"Data/Hora : {data_execucao}")
print("==============================")

# ── 1. Filiais ──────────────────────────────────────────────────────────────
print("\nBuscando filiais...")
branches   = get_branches(TOKEN_MOTTU)
branch_ids = [b["id"] for b in branches]
print(f"[✔] Filiais encontradas: {len(branches)}")

# ── 2. Veículos ─────────────────────────────────────────────────────────────
print("\nBuscando veículos...")
vehicles = get_vehicles(TOKEN_MOTTU, branch_ids)
print(f"[✔] Veículos encontrados: {len(vehicles)}")

# ── 3. Extrai placas únicas e válidas ───────────────────────────────────────
plates = list({
    v["plate"].strip()
    for v in vehicles
    if v.get("plate") and v["plate"].strip()
})
print(f"[✔] Placas únicas: {len(plates)}")

if LIMITE_PLACAS:
    plates = plates[:LIMITE_PLACAS]
    print(f"[!] Limite de teste ativo: {LIMITE_PLACAS} placas")

# ── 4. Consulta multas e cria cards ─────────────────────────────────────────
print("\n==============================")
print("CONSULTANDO MULTAS")
print("==============================")

session = requests.Session()

for i, plate in enumerate(plates, start=1):

    print(f"\n[{i}/{len(plates)}] Placa: {plate}")
    total_placas_consultadas += 1

    fines = get_fines(plate, session)

    if not fines:
        print("  — Sem multas pendentes")
        time.sleep(PAUSA_SEGUNDOS)
        continue

    print(f"  [✔] {len(fines)} multa(s) encontrada(s)")
    total_multas_encontradas += len(fines)

    for fine in fines:
        monto     = fine.get("monto", 0)
        descuento = fine.get("descuento", 0)
        total     = calcular_total_a_pagar(monto, descuento)

        print(f"    Boleta        : {fine.get('boleta', '')}")
        print(f"    Orden de Pago : {fine.get('orden_de_pago', '')}")
        print(f"    Data          : {fine.get('fecha_infraccion', '')}")
        print(f"    Monto         : ${monto}")
        print(f"    Descuento     : ${descuento}")
        print(f"    Total a Pagar : ${total}")
        print(f"    Local         : {fine.get('descripcion', '')}")

        card = create_pipefy_card(fine)
        if card:
            print(f"    [✔] Card criado: {card.get('id')} | {card.get('title')}")
            print(f"        URL: {card.get('url')}")
        else:
            print(f"    [✖] Falha ao criar card")

    time.sleep(PAUSA_SEGUNDOS)


# ── 5. Resumo final ─────────────────────────────────────────────────────────
print("\n==============================")
print("RESUMO FINAL")
print("==============================")
print(f"Placas consultadas  : {total_placas_consultadas}")
print(f"Multas encontradas  : {total_multas_encontradas}")
print(f"Cards criados       : {total_cards_criados}")
print(f"Total de erros      : {total_erros}")
print("==============================\n")
