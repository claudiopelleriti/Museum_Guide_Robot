import os
import json
import re
import uvicorn
import math
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
from langchain_neo4j import Neo4jGraph, GraphCypherQAChain
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate

# --- CONFIGURAZIONE ---
os.environ["GROQ_API_KEY"] = "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"

NEO4J_URL = "bolt://127.0.0.1:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "123456789"
NEO4J_DB = "abc"

REL_ROBOT_POSIZIONE = "SI_TROVA_IN"

app = FastAPI()

# --- MODELLI PER COMUNICAZIONE UNITY ---
class TargetPos(BaseModel):
    x: float
    y: float
    z: float
    theta: float

class UnityMessage(BaseModel):
    text: str
    battery_level: float = 100.0

class UnityResponse(BaseModel):
    text: str
    action: str = "TALK"
    target: str = ""
    robot_message: str = ""
    status: str = "TALK"
    target_pos: Optional[TargetPos] = None

class RoboGuida:
    def __init__(self):
        self.graph = Neo4jGraph(url=NEO4J_URL, username=NEO4J_USER, password=NEO4J_PASS, database=NEO4J_DB)
        self.llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
        self.qa_chain = self._setup_qa_chain()
        
        self.state = "IDLE" 
        self.tour_path = [] 
        self.current_step_index = 0
        self.visitor_time_budget = 9999 
        self.chat_history = []
        
        self.tour_path_base = []
        self.tour_path_extended = []
        self.explanations = {}
        
        # Gestione opera suggerita durante QA
        self.pending_artwork = None 
        
        self._init_base_nodes()
        self._debug_db_check()

    def _init_base_nodes(self):
    # Eseguiamo tutto in una sola chiamata per efficienza
        query = """
        // 1. Pulisci tutto (Attenzione: cancella dati di sessioni precedenti)
        MATCH (v:Visitatore) OPTIONAL MATCH (v)-[r]-() DELETE r, v
        WITH count(v) as dummy1 
        MATCH (p:Profilo) OPTIONAL MATCH (p)-[r]-() DELETE r, p
        
        // 2. Ricrea le basi (tutto in una volta)
        MERGE (:Robot {name: 'Sophia'})
        MERGE (:Visitatore {id: 'current_visitor'})
        MERGE (:Ingresso {name: 'Ingresso'})
        """
        self.graph.query(query)
        
        # 3. Muovi
        self._move_robot_logic("Ingresso")

    def _debug_db_check(self):
        print("\n--- CONTROLLO CONTENUTO DATABASE ---")
        # 1. Vediamo un esempio di nodo Opera per capire le proprietà (name vs title)
        check_props = self.graph.query("MATCH (o:Opera) RETURN keys(o) as props, o.name as nome, labels(o) as labels LIMIT 1")
        if check_props:
            print(f"Esempio Opera trovata: {check_props}")
        else:
            print("ATTENZIONE: Nessun nodo con label :Opera trovato!")
            # Proviamo a cercare qualsiasi cosa per vedere le label
            any_node = self.graph.query("MATCH (n) RETURN labels(n) as labels, n.name as name LIMIT 5")
            print(f"Primi 5 nodi generici nel DB: {any_node}")

        # 2. Stampiamo i nomi di 10 opere a caso per vedere come sono scritte
        names = self.graph.query("MATCH (o:Opera) RETURN o.name as nome LIMIT 20")
        print("Elenco nomi opere nel DB:")
        for r in names:
            print(f"- {r['nome']}")
        print("------------------------------------\n")

    def _setup_qa_chain(self):
        template_cypher = """
        Task: Generate a Cypher query to answer the question using the provided Schema.
        Schema: {schema}
        
        MANDATORY RULES:
        1. Artwork names in DB might not include articles (e.g., 'Gioconda' instead of ' La Gioconda'). Use 'CONTAINS' for name matching to be safe.
        2. To find "similar" works, look for the same Artista OR the same MovimentoArtistico.
        3. Exclude titles already in the tour using: WHERE NOT o.name IN {excluded_titles}.
        4. Quando costruisci le query cypher non inserire la direzione delle relazioni (es. Opera-[ESPONE]-(Sala) senza -> o <-).
        5. Always replace ' with ’ in artwork name.
        
        MEMORY (EXCLUDE THESE): {excluded_titles}
        
        Question: {question}
        Cypher Query:"""
        PROMPT_CYPHER = PromptTemplate(input_variables=["schema", "question", "excluded_titles"], template=template_cypher)
        return GraphCypherQAChain.from_llm(llm=self.llm, graph=self.graph, verbose=True, cypher_prompt=PROMPT_CYPHER, allow_dangerous_requests=True)
    
    def _parse_time(self, time_str):
        if not time_str or time_str == "infinito": return 9999
        match = re.search(r'(\d+)', str(time_str))
        val = int(match.group(1)) if match else 0
        lower_s = str(time_str).lower()
        if 'ora' in lower_s or 'ore' in lower_s: val *= 60
        return val if val > 0 else 9999

    def _update_visitor_db(self, name, age, tempo_int):
        age_int = int(age) if str(age).isdigit() else 18
        # CAMBIATO MATCH IN MERGE
        query = """
        MERGE (v:Visitatore {id: 'current_visitor'}) 
        SET v.name = $name, v.eta = $age, v.tempo_disponibile = $tempo
        RETURN v
        """
        self.graph.query(query, params={"name": name, "age": age_int, "tempo": tempo_int})
        self.visitor_time_budget = tempo_int
        
    def _link_interest_to_visitor(self, interest_string):
        # 1. Pulizia Input lato Python:
        # Assicuriamoci che l'input che mandiamo abbia l'apostrofo standard
        val_normalizzato = interest_string.replace("’", "'")

        # 2. Query con normalizzazione al volo
        query = """
        MERGE (v:Visitatore {id: 'current_visitor'})
        WITH v
        MATCH (target) 
        WHERE (target:Artista OR target:MovimentoArtistico OR target:Opera)
        AND (
            toLower(replace(target.name, '’', "'")) 
            CONTAINS 
            toLower($val)
        )
        MERGE (v)-[:INTERESSATO_A]->(target)
        RETURN labels(target) as tipo, target.name as name
        """
        
        # 3. Usiamo params! Questo impedisce il crash di sintassi
        return self.graph.query(query, params={"val": val_normalizzato})
    
    def _propose_next_targets(self) -> UnityResponse:
        """
        Invia a Unity la lista delle opere RIMASTE da visitare.
        LOGICA INTELLIGENTE: 
        1. Se ci sono ancora opere nella sala CORRENTE, invia SOLO quelle (evita il ping-pong tra stanze).
        2. Se la sala è finita, invia TUTTE le opere rimanenti e lascia che Unity scelga la più vicina.
        """
        if not self.tour_path or self.current_step_index >= len(self.tour_path):
            self.state = "FINAL_QA"
            msg = "SOPHIA: Il nostro tour termina qui. Hai altre domande o vuoi che torni all'ingresso?"
            return UnityResponse(text=msg, robot_message=msg, action="TALK")

        # 1. Identifichiamo la "Sala Corrente" (quella dell'ultima opera visitata)
        current_room = None
        if self.current_step_index > 0:
            # L'ultima visitata è all'indice precedente
            last_opera = self.tour_path[self.current_step_index - 1]
            current_room = last_opera['sala']
            print(f"DEBUG: Il robot si trova fisicamente in: {current_room}")
        else:
            print("DEBUG: Inizio del tour (o Ingresso).")

        # 2. Raccogliamo le candidate
        candidates_same_room = [] # Opere rimaste nella stessa stanza
        candidates_all_others = [] # Tutte le opere rimaste ovunque

        # Iteriamo su tutte le opere future
        for i in range(self.current_step_index, len(self.tour_path)):
            opera = self.tour_path[i]
            coords = self._get_artwork_coords(opera['titolo'])
            
            cand_obj = {
                "titolo": opera['titolo'],
                "x": coords.x,
                "y": coords.y,
                "z": coords.z,
                "theta": coords.theta,
                "sala": opera['sala'] # (Per debug interno)
            }

            candidates_all_others.append(cand_obj)

            if current_room and opera['sala'] == current_room:
                candidates_same_room.append(cand_obj)
        
        # 3. DECISIONE STRATEGICA
        final_candidates = []

        if candidates_same_room:
            # CASO A: PRIORITÀ SALA
            # Ci sono ancora opere in questa sala. Costringiamo Unity a finire la sala.
            print(f"DEBUG: Trovate {len(candidates_same_room)} opere ancora in {current_room}. Resto qui.")
            final_candidates = candidates_same_room
        else:
            # CASO B: CAMBIO SALA
            # La sala è finita (o siamo all'inizio). Inviamo TUTTO.
            # Unity, grazie al tuo script C#, calcolerà le distanze verso tutte le opere.
            # La più vicina sarà necessariamente in una delle stanze adiacenti.
            print(f"DEBUG: Nessuna opera rimasta in sala corrente. Invio {len(candidates_all_others)} opzioni globali a Unity.")
            final_candidates = candidates_all_others
        
        json_candidates = json.dumps(final_candidates)
        
        return UnityResponse(
            text="Calcolo la prossima tappa...", 
            robot_message="", 
            action="DECIDE_BEST_PATH", 
            target=json_candidates
        )

    def _move_robot_logic(self, target_name):
        query = f"""
        MATCH (r:Robot {{name: 'Sophia'}}) OPTIONAL MATCH (r)-[old:{REL_ROBOT_POSIZIONE}]->() DELETE old
        WITH r MATCH (target) 
        WHERE (target:Sala AND target.name = $room) 
           OR (target:Ingresso AND $room = 'Ingresso')
           OR (target:Ingresso AND target.name = 'Ingresso' AND $room = 'Ingresso')
        MERGE (r)-[:{REL_ROBOT_POSIZIONE}]->(target)
        RETURN labels(target) as tipo, target.name as nome
        """
        self.graph.query(query, params={"room": target_name})

    def _get_artwork_coords(self, target_name):
        query = "MATCH (o:Opera {name: $name}) RETURN o.x as x, o.y as y, o.z as z, o.orientazione_y as theta"
        res = self.graph.query(query, params={"name": target_name})
        if res and res[0]['x'] is not None:
            return TargetPos(x=res[0]['x'], y=res[0]['y'], z=res[0]['z'], theta=res[0]['theta'])
        return TargetPos(x=0, y=0, z=0, theta=0)

    def _reset_visitor_data(self):
        query = """
        MATCH (v:Visitatore {id: 'current_visitor'})
        OPTIONAL MATCH (v)-[r:INTERESSATO_A]->()
        MATCH (o:Opera)
        DELETE r
        SET v = {id: 'current_visitor'}
        SET o.gia_descritta = false
        """
        self.graph.query(query)
        self.chat_history = []

    def _generate_narrative_path(self, path_type):
        path = self.tour_path_base if path_type == 1 else self.tour_path_extended
        
        dati_per_llm = [] 
        for o in path:
            m = self.explanations.get(o['titolo'], "opera di tuo interesse")
            dati_per_llm.append(f"- {o['titolo']} (Sala {o['sala']}): {m}")

        opere_str = "\n".join(dati_per_llm)

        prompt = f"""
        Sei una guida museale robotica. Presenta il percorso al visitatore in modo piuttosto sintetico elencando singolarmente tutte le opere del percorso, in modo discorsivo, affiancando ciascuna alla spiegazione del perchè è stata scelta nel tour in base agli interessi mostrati dal visitatore. Non spiegare l'ordine con cui verranno viste le opere, perchè questo potrebbe cambiare, spiega solo il perchè, di alla fine che l'ordine sarà determinato in base al percorso più breve.
        
        REGOLE MANDATORIE:
        1. Non descrivere le opere ma solo il percorso.
        2. Se più opere hanno la stessa motivazione (es. stesso artista), raggruppale nella stessa motivazione, trovando e specificando sempre un collegamento tra la motivazione e i suoi interessi.
        4. Non salutare all'inizio.
        5. Se c'è solo un'opera, rispondi con una sola frase breve.

        OPERE E MOTIVAZIONI PER CUI SONO STATE SCELTE:
        {opere_str}
        """
        
        risposta = self.llm.invoke(prompt).content.strip()
        return risposta

    def _create_tour_path(self):
        """
        Crea un percorso ibrido:
        1. Identifica separatamente interessi Espliciti (priorità alta) e Derivati (priorità bassa).
        2. Riempie prima con gli Espliciti usando il ciclo while (codice originale Priorità 2).
        3. Se avanza tempo, espande con i Derivati usando la query massiva (codice originale Priorità 3).
        """
        # 1. Recupero di TUTTI gli interessi
        q_interessi = "MATCH (v:Visitatore {id: 'current_visitor'})-[:INTERESSATO_A]->(t) RETURN labels(t) as lbs, t.name as name"
        interessi_db = self.graph.query(q_interessi)
        
        self.tour_path_base, self.tour_path_extended, self.explanations = [], [], {}
        seen_titles, time_acc = set(), 0
        
        # --- SEPARAZIONE NETTA DELLE LISTE (Logica Nuova per garantire priorità) ---
        # Set per interessi ESPLICITI (es. utente dice "Impressionismo")
        artisti_espliciti = set()
        movimenti_espliciti = set()
        
        # Set per interessi DERIVATI (es. utente dice "Gioconda" -> aggiungiamo Leonardo qui)
        artisti_derivati = set()
        movimenti_derivati = set()

        # FASE 0: CLASSIFICAZIONE E BASE PATH
        if not interessi_db:
            # Fallback: capolavori generali (Tuo codice originale)
            generali = self.graph.query("MATCH (o:Opera)<-[:ESPONE]-(s:Sala) RETURN o.name as t, s.name as s, coalesce(o.tempo_visita_minuti, 5) as d ORDER BY o.name")
            for o in generali:
                if (time_acc + o['d']) <= self.visitor_time_budget:
                    self.tour_path_base.append({'titolo': o['t'], 'sala': o['s'], 'durata': o['d']})
                    seen_titles.add(o['t'])
                    time_acc += o['d']
                    self.explanations[o['t']] = "un capolavoro del museo"
            self.tour_path_extended = list(self.tour_path_base)
            return True

        # Analisi Interessi (Tuo codice originale adattato per popolare i set separati)
        for item in interessi_db:
            if 'Opera' in item['lbs']:
                res = self.graph.query("""
                    MATCH (o:Opera {name: $n})<-[:ESPONE]-(s:Sala)
                    OPTIONAL MATCH (o)-[:REALIZZATA_DA]->(a:Artista)
                    OPTIONAL MATCH (o)-[:APPARTIENE_A]->(m:MovimentoArtistico)
                    RETURN o.name as t, s.name as s, coalesce(o.tempo_visita_minuti, 5) as d, a.name as artista, m.name as movimento
                """, params={"n": item['name']})
                
                if res and res[0]['t'] not in seen_titles:
                    o = res[0]
                    if (time_acc + o['d']) <= self.visitor_time_budget:
                        self.tour_path_base.append({'titolo': o['t'], 'sala': o['s'], 'durata': o['d']})
                        seen_titles.add(o['t'])
                        time_acc += o['d']
                        self.explanations[o['t']] = "l'opera che hai richiesto di vedere"
                        
                        # QUI LA DIFFERENZA: Se vengono da un'opera, sono DERIVATI (bassa priorità)
                        if o['artista']: artisti_derivati.add(o['artista'])
                        if o['movimento']: movimenti_derivati.add(o['movimento'])
            
            elif 'Artista' in item['lbs']:
                # Se richiesti direttamente, sono ESPLICITI (alta priorità)
                artisti_espliciti.add(item['name'])
            elif 'MovimentoArtistico' in item['lbs']:
                movimenti_espliciti.add(item['name'])

        # Inizializziamo l'esteso
        self.tour_path_extended = list(self.tour_path_base)
        time_ext = time_acc

        # --- FASE 1: CATEGORIE ESPLICITE (ALTA PRIORITÀ) ---
        # Usiamo esattamente il TUO blocco "PRIORITÀ 2" originale, ma lo alimentiamo SOLO con le liste esplicite.
        
        categorie_priority = [n for n in (list(artisti_espliciti) + list(movimenti_espliciti))]
        
        while time_ext < self.visitor_time_budget and categorie_priority:
            added_in_round = False
            for cat in categorie_priority:
                # Tuo codice originale identico
                q_op = """
                MATCH (target {name: $n})<-[:REALIZZATA_DA|APPARTIENE_A]-(o:Opera)<-[:ESPONE]-(s:Sala)
                WHERE NOT o.name IN $seen
                RETURN o.name as t, s.name as s, coalesce(o.tempo_visita_minuti, 5) as d, labels(target)[0] as tipo
                LIMIT 1
                """ 
                # LIMIT 1 aggiunto per non esaurire subito il tempo con un solo artista se ce ne sono altri
                res = self.graph.query(q_op, params={"n": cat, "seen": list(seen_titles)})
                if res:
                    o = res[0]
                    if (time_ext + o['d']) <= self.visitor_time_budget:
                        self.tour_path_extended.append({'titolo': o['t'], 'sala': o['s'], 'durata': o['d']})
                        seen_titles.add(o['t'])
                        time_ext += o['d']
                        # TUA spiegazione originale per gli interessi diretti
                        self.explanations[o['t']] = f"realizzata da {cat}" if o['tipo'] == 'Artista' else f"parte del movimento {cat}"
                        added_in_round = True
            if not added_in_round: break

        # --- FASE 2: CATEGORIE DERIVATE (BASSA PRIORITÀ) ---
        # Usiamo esattamente il TUO blocco "PRIORITÀ 3" (ESPANSIONE) originale.
        # Lo alimentiamo con le liste derivate (quelle estratte dalla Gioconda, ecc.)
        
        # Pulizia: non cercare cose che erano già esplicite (evita duplicati logici)
        artisti_finali = [a for a in artisti_derivati if a not in artisti_espliciti]
        movimenti_finali = [m for m in movimenti_derivati if m not in movimenti_espliciti]

        if time_ext < self.visitor_time_budget and (artisti_finali or movimenti_finali):
            # TUA query originale q_extra
            q_extra = """
            MATCH (o:Opera)<-[:ESPONE]-(s:Sala)
            WHERE NOT o.name IN $seen
            OPTIONAL MATCH (o)-[:REALIZZATA_DA]->(a:Artista)
            OPTIONAL MATCH (o)-[:APPARTIENE_A]->(m:MovimentoArtistico)
            WHERE a.name IN $artisti OR m.name IN $movimenti
            RETURN o.name as t, s.name as s, coalesce(o.tempo_visita_minuti, 5) as d, a.name as artista, m.name as movimento
            """
            suggeriti = self.graph.query(q_extra, params={
                "seen": list(seen_titles), 
                "artisti": artisti_finali, 
                "movimenti": movimenti_finali
            })
            
            for sug in suggeriti:
                # Controllo budget tempo
                if time_ext >= self.visitor_time_budget: break
                
                if (time_ext + sug['d']) <= self.visitor_time_budget:
                    self.tour_path_extended.append({'titolo': sug['t'], 'sala': sug['s'], 'durata': sug['d']})
                    seen_titles.add(sug['t'])
                    time_ext += sug['d']
                    
                    # TUA logica di spiegazione originale che ti piaceva
                    # Nota: usiamo artisti_finali per il check
                    if sug['artista'] in artisti_finali:
                        self.explanations[sug['t']] = f"realizzata dallo stesso artista ({sug['artista']})"
                    else:
                        self.explanations[sug['t']] = f"esponente del movimento {sug['movimento']}"

        return True

    def _process_profiling(self, text):
        print(f"\n--- INIZIO DEBUG PROFILING ---")
        print(f"DEBUG - Input utente: '{text}'")

        # 1. Recupero persistente del tempo dal DB
        current_data = self.graph.query(
            "MATCH (v:Visitatore {id: 'current_visitor'}) RETURN v.tempo_disponibile as t, v.name as n"
        )
        db_time = current_data[0]['t'] if current_data and current_data[0]['t'] else 9999
        db_name = current_data[0]['n'] if current_data and current_data[0]['n'] else "Ospite"

        #- "azione": "RESET" se l'utente vuole cambiare completamente interessi, "ADD" per aggiungere ai precedenti.
        
        # 2. Prompt
        prompt = f"""Analizza l'input del visitatore museo: "{text}"
        Genera un JSON con questi campi:
        - "nome": stringa o null
        - "eta": numero o null
        - "tempo": numero di minuti (solo intero) o null
        - "interessi": lista di artisti, opere o movimenti.

        REGOLE: Rispondi ESCLUSIVAMENTE con il JSON, senza spiegazioni. Correggi eventuali errori di battitura (es. "Giconda" -> "Gioconda").
        """
        
        # Inizializziamo a None per evitare crash nel blocco except
        raw_response = None 
        
        try:
            # Chiamata LLM
            raw_response = self.llm.invoke(prompt).content.strip()
            print(f"DEBUG - Risposta LLM grezza:\n{raw_response}") # <--- VEDI COSA RISPONDE L'LLM

            # Pulizia JSON
            clean_json = raw_response.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_json)
            
            # LOG FONDAMENTALE: Vediamo cosa ha capito l'LLM
            print(f"DEBUG - JSON Parsato: {data}")
            print(f"DEBUG - Lista Interessi Estratti: {data.get('interessi')}") 

        except Exception as e:
            print(f"ERROR - Errore parsing LLM: {e}")
            if raw_response:
                print(f"ERROR - Risposta che ha causato l'errore: {raw_response}")
            
            # Fallback
            data = {"nome": None, "eta": None, "tempo": None, "interessi": []}

        # 3. Gestione Tempo
        tempo_minuti = int(data.get('tempo')) if data.get('tempo') else db_time
        self.visitor_time_budget = tempo_minuti

        # 5. Aggiornamento DB
        nome_finale = data.get('nome') or db_name
        self._update_visitor_db(nome_finale, data.get('eta') or 18, tempo_minuti)
        
        interessi_trovati = []
        lista_interessi = data.get('interessi') or []
        
        print(f"DEBUG - Inizio ricerca nel DB per: {lista_interessi}")

        for item in lista_interessi:
            # Qui vediamo se il match funziona
            matches = self._link_interest_to_visitor(item)
            
            if matches:
                print(f"DEBUG - TROVATO match nel DB per '{item}': {matches}")
                interessi_trovati.append(item)
            else:
                print(f"DEBUG - NESSUN match nel DB per '{item}'")
        
        print(f"--- FINE DEBUG PROFILING ---\n")

        tempo_disponibile = f"hai {tempo_minuti} minuti a disposizione" if tempo_minuti < 9999 else "non hai limiti di tempo"
        return f"SOPHIA: Caro {nome_finale}. Dato che {tempo_disponibile} ti propongo i seguenti tour."
    
    def _get_charging_station_coords(self):
        """Recupera le coordinate esatte della base dal DB"""
        query = "MATCH (b:BaseDiRicarica) RETURN b.x as x, b.y as y, b.z as z, b.theta as theta"
        res = self.graph.query(query)
        if res:
            return TargetPos(x=res[0]['x'], y=res[0]['y'], z=res[0]['z'], theta=res[0]['theta'])
        return TargetPos(x=0, y=0, z=0, theta=0) # Fallback

    def handle_unity_input(self, user_input: str, battery: float) -> UnityResponse:
        text = user_input.strip()
        
        # Logica ricarica immediata se IDLE
        if self.state == "IDLE" and battery <= 30.0:
            coords = self._get_charging_station_coords()
            return UnityResponse(
                text="Batteria scarica. Vado alla base.",
                action="GO_TO_CHARGER",
                target_pos=coords,
                status="CHARGING"
            )
        
        # 1. GESTIONE SCELTA PERCORSO (Il robot ha deciso dove andare)
        if text.startswith("CHOICE:"):
            opera_scelta = text.split(":")[1].strip()
            print(f"DEBUG: Il robot ha scelto di andare a: {opera_scelta}")
            
            # --- FIX FONDAMENTALE: SINCRONIZZAZIONE LISTA ---
            # Unity ha scelto un'opera che potrebbe non essere la prima della lista Python.
            # Dobbiamo trovare quell'opera nella lista e scambiarla con quella alla posizione corrente.
            if self.tour_path:
                for i in range(self.current_step_index, len(self.tour_path)):
                    if self.tour_path[i]['titolo'] == opera_scelta:
                        # Trovata! Scambiamola con l'opera corrente
                        self.tour_path[self.current_step_index], self.tour_path[i] = \
                        self.tour_path[i], self.tour_path[self.current_step_index]
                        print(f"DEBUG: Lista riordinata. Prossima tappa confermata: {self.tour_path[self.current_step_index]['titolo']}")
                        break
            
            return UnityResponse(
                text=f"SOPHIA: Ottima scelta! Andiamo subito verso: {opera_scelta}. Seguimi!",
                robot_message=f"SOPHIA: Andiamo verso {opera_scelta}.",
                action="TALK", 
                target="" # Stop loop Unity
            )

        # 2. GESTIONE ARRIVO (Il robot è arrivato davanti all'opera)
        if text.startswith("ARRIVED:"):
            opera_corrente = text.split(":")[1].strip()
            print(f"DEBUG: Il robot è arrivato a: {opera_corrente}. Genero descrizione...")
            
            # Recuperiamo l'età
            res_visitatore = self.graph.query("MATCH (v:Visitatore {id: 'current_visitor'}) RETURN v.eta as eta")
            eta_visitatore = res_visitatore[0]['eta'] if res_visitatore and res_visitatore[0]['eta'] else 18
            
            # Prompt Descrizione
            prompt = (
                f"Sei una guida museale. Sei appena arrivata davanti all'opera '{opera_corrente}'. "
                f"Descrivila al visitatore (che ha {eta_visitatore} anni). "
                f"Sii sintetico nella descrizione ma non troppo"
                f"Inizia dicendo 'Eccoci arrivati davanti a...'"
            )
            risposta = self.llm.invoke(prompt).content.strip()
            
            # --- FIX FONDAMENTALE: IMPOSTARE LO STATO QA ---
            # Ora che siamo arrivati e abbiamo descritto, siamo pronti per le domande o per 'avanti'
            self.state = "QA"  # <--- QUESTO MANCAVA E CAUSAVA IL BUG DI "AVANTI"
            
            return UnityResponse(
                text=risposta,
                robot_message=risposta,
                action="TALK"
            )
        
        # --- FINE BLOCCO NAVIGAZIONE ---

        # --- GESTIONE CONF_ADD ---
        if self.state == "CONFIRM_ADDITION":
            if text.lower() in ['si', 'sì', 'yes', 'ok']:
                self.tour_path.insert(self.current_step_index + 1, self.pending_artwork)
                msg = f"SOPHIA: Ottima scelta! Ho aggiornato il tour. Andremo a vedere '{self.pending_artwork['titolo']}' subito dopo questa spiegazione. Scrivi 'avanti' quando vuoi procedere."
                self.pending_artwork = None
                self.state = "QA"
                return UnityResponse(text=msg, robot_message=msg)
            else:
                self.pending_artwork = None
                self.state = "QA"
                return UnityResponse(text="SOPHIA: Va bene, proseguiamo pure con il percorso stabilito. Hai altre domande?", robot_message="SOPHIA: Va bene, proseguiamo pure.")

        # --- GESTIONE FINE TOUR (QA FINALE) ---
        if self.state == "FINAL_QA":
            # Se l'utente saluta o il robot decide di chiudere
            intent_prompt = f"L'utente ha detto: '{text}'. Vuole terminare? Rispondi solo CLOSE o ASK."
            intent_res = self.llm.invoke(intent_prompt).content.strip().upper()
            
            if "CLOSE" in intent_res:
                self._reset_visitor_data()
                self.state = "IDLE"
                
                # CONTROLLO BATTERIA A FINE VISITA
                if battery <= 30.0:
                    coords = self._get_charging_station_coords()
                    msg = "SOPHIA: Il tour è finito. La mia batteria è bassa, vado a ricaricarmi. A presto!"
                    return UnityResponse(text=msg, robot_message=msg, action="GO_TO_CHARGER", target_pos=coords)
                else:
                    msg = "SOPHIA: È stato un piacere! Torno all'ingresso."
                    return UnityResponse(text=msg, robot_message=msg, action="RETURN_TO_ENTRANCE")
            else:
                return self._handle_dynamic_qa_unity(text)

        # --- IDLE ---
        if self.state == "IDLE":
            if text.lower() in ['ciao', 'hello', 'start', 'inizio']:
                self.state = "PROFILING"
                # ... (codice esistente per il benvenuto) ...
                sophia = self.graph.query("MATCH (r:Robot) RETURN r.descrizione as descrizione")
                desc = sophia[0]["descrizione"] if sophia else "sono la tua guida"
                msg = f"SOPHIA: Buongiorno! Mi chiamo Sophia, {desc}. Per cominciare dimmi un po' di te: come ti chiami, quanti anni hai, quali sono i tuoi interessi o se hai limiti di tempo?"
                return UnityResponse(text=msg, robot_message=msg)
            else:
                # --- FIX CRASH 500 ---
                try:
                    excluded = [o['titolo'] for o in self.tour_path]
                    res = self.qa_chain.invoke({
                        "query": user_input, 
                        "excluded_titles": str(excluded)
                    })
                    return UnityResponse(text=f"SOPHIA: {res['result']}", robot_message=f"SOPHIA: {res['result']}")
                except Exception as e:
                    print(f"ERROR: LLM ha fallito la generazione Cypher: {e}")
                    # Risposta di fallback gentile invece di crashare
                    fallback = "SOPHIA: Scusami, in questo momento sto riorganizzando il mio database. Puoi ripetere la domanda o scrivermi 'ciao' per iniziare un tour?"
                    return UnityResponse(text=fallback, robot_message=fallback)
        
        # --- PROFILING ---
        elif self.state == "PROFILING" or (self.state == "CONFIRM_PATH" and text.lower() not in ["1", "2"]):
            res_profiling = self._process_profiling(text) 
            self.state = "PLANNING"
            self._create_tour_path()
            self.state = "CONFIRM_PATH"
            
            p1 = self._generate_narrative_path(1)
            p2 = self._generate_narrative_path(2) if len(self.tour_path_extended) > len(self.tour_path_base) else ""

            messaggio = f"{res_profiling}\n\n"
            if self.tour_path_base:
                messaggio += f"OPZIONE 1 (Percorso Mirato): {p1}\n\n"
                if p2: messaggio += f"OPZIONE 2 (Percorso Completo): {p2}\n\n"
                messaggio += "Digita '1' o '2' per scegliere, oppure aggiungi altri interessi."
            else:
                messaggio += f"Ho preparato un percorso basato sui tuoi interessi:\n\n{p2}\n\n"
                messaggio += "Digita '2' per confermare questo percorso, oppure aggiungi altri interessi."

            return UnityResponse(text=messaggio, robot_message=messaggio)

        # --- CONFIRM_PATH ---
        elif self.state == "CONFIRM_PATH":
            if text in ["1", "2"]:
                self.tour_path = list(self.tour_path_base if text == "1" else self.tour_path_extended)
                # Resettiamo l'indice
                self.current_step_index = 0
                return self._propose_next_targets()
            else:
                self.state = "PROFILING"
                return self.handle_unity_input(user_input)
            
        # --- MOVING (NON USATO PIU' PERCHE' USIAMO ARRIVED, MA LASCIAMOLO PER SICUREZZA) ---
        elif self.state == "MOVING":
             # Questo blocco ora è ridondante perché usiamo ARRIVED, ma lo lasciamo 
             # nel caso qualcosa vada storto e non arrivi l'evento.
             return UnityResponse(text="SOPHIA: Sto andando...", robot_message="...")

        # --- QA TOUR ---
        elif self.state == "QA":
            if text.lower() in ['avanti', 'next', 'prossima', 'ok', 'andiamo']:
                self.current_step_index += 1
                if self.current_step_index >= len(self.tour_path):
                    self.state = "FINAL_QA"
                    msg = "SOPHIA: Il nostro tour termina qui. Hai altre domande su queste opere o vuoi che torni all'ingresso?"
                    return UnityResponse(text=msg, robot_message=msg)
                else:
                    # --- FIX: INVECE DI FORZARE IL MOVIMENTO, PROPONIAMO DI NUOVO I TARGET ---
                    # Così Unity ricalcola qual è il più vicino tra quelli RIMASTI
                    return self._propose_next_targets()
            else:
                return self._handle_dynamic_qa_unity(text)

        return UnityResponse(text="SOPHIA: Sono pronta.", robot_message="SOPHIA: Sono pronta.")

    def _trigger_move(self) -> UnityResponse:
        self.state = "MOVING"
        current_step = self.tour_path[self.current_step_index]
        self._move_robot_logic(current_step['sala'])
        coords = self._get_artwork_coords(current_step['titolo'])
        msg = f"SOPHIA: Adesso andiamo a vedere '{current_step['titolo']} che si trova in {current_step['sala']}'. Seguimi!"
        return UnityResponse(text=msg, robot_message=msg, status="MOVING", target_pos=coords)

    def _handle_dynamic_qa_unity(self, user_input) -> UnityResponse:
        """
        Gestisce le domande dinamiche con memoria temporale (Passato/Futuro) 
        e logica di intent detection migliorata.
        Separazione netta tra stato del tour (progressione) e motivazioni (contenuto).
        """
        # --- 1. MEMORIA DEL PERCORSO ---
        # Dividiamo il percorso in base a dove ci troviamo ora
        opere_visitate = self.tour_path[:self.current_step_index]
        nomi_visitati = [o['titolo'] for o in opere_visitate]

        # Includiamo l'opera corrente nelle future/presenti
        opere_future = self.tour_path[self.current_step_index:]
        nomi_futuri = [o['titolo'] for o in opere_future]
        
        # Per sicurezza (evita IndexError)
        idx = min(self.current_step_index, len(self.tour_path) - 1)
        current_art_exact = self.tour_path[idx]['titolo'] if self.tour_path else "Ingresso"
        
        # Aggiornamento chat history
        self.chat_history.append(f"Visitatore: {user_input}")
        recent_history = "\n".join(self.chat_history[-6:])
        
        # --- 2. RICONOSCIMENTO INTENTO ---
        intent_prompt = (
            f"Storico recente:\n{recent_history}\n"
            f"Analizza l'input: \"{user_input}\". "
            f"L'utente vuole esplicitamente andare a vedere un'altra opera specifica ORA? "
            f"Rispondi SOLO JSON: {{\"vuole_vedere_opera\": true/false, \"nome_opera\": \"stringa o null\"}}"
        )
        
        try:
            intent_res = self.llm.invoke(intent_prompt).content
            clean_json = intent_res.replace("```json", "").replace("```", "").strip()
            if "{" not in clean_json: clean_json = "{}" 
            intent_data = json.loads(clean_json)
            
            if intent_data.get("vuole_vedere_opera") and intent_data.get("nome_opera"):
                nome_richiesto = intent_data["nome_opera"]
                
                db_res = self.graph.query(
                    "MATCH (o:Opera)<-[:ESPONE]-(s:Sala) "
                    "WHERE toLower(o.name) CONTAINS toLower($n) "
                    "RETURN o.name as titolo, s.name as sala", 
                    params={"n": nome_richiesto}
                )
                
                if db_res:
                    found = db_res[0]
                    found_title = found['titolo']
                    
                    if found_title in nomi_visitati:
                        msg = f"SOPHIA: Abbiamo già visitato '{found_title}' poco fa. Se vuoi posso parlartene ancora, ma fa già parte dei nostri ricordi di oggi!"
                        return UnityResponse(text=msg, robot_message=msg)

                    elif found_title in nomi_futuri:
                        if found_title == current_art_exact:
                             msg = f"SOPHIA: Siamo proprio qui davanti a '{found_title}' adesso!"
                        else:
                             msg = f"SOPHIA: Ottima scelta! '{found_title}' è già prevista nel nostro percorso tra poco."
                        return UnityResponse(text=msg, robot_message=msg)
                    
                    else:
                        self.pending_artwork = found
                        self.state = "CONFIRM_ADDITION"
                        msg = f"SOPHIA: Ho trovato '{found_title}' in {found['sala']}. Vuoi deviare il percorso e andarla a vedere subito?"
                        return UnityResponse(text=msg, robot_message=msg)

            # --- 3. QA CHAIN (Risposte alle domande) ---
            
            # A. Costruzione Liste Separate
            lista_stato = []
            lista_motivazioni = []

            for o in self.tour_path:
                t = o['titolo']
                # Determina lo stato
                if t in nomi_visitati:
                    status = "(GIÀ VISITATA)"
                elif t == current_art_exact:
                    status = "(SIAMO QUI ORA)"
                else:
                    status = "(DA VEDERE)"
                
                # Aggiunge alla lista stato
                lista_stato.append(f"- {t} {status}")

                # Recupera spiegazione e aggiunge alla lista motivazioni
                motivo = self.explanations.get(t, "opera selezionata per te")
                lista_motivazioni.append(f"- {t}: {motivo}")
            
            # B. Creazione stringhe finali
            str_stato_tour = "\n".join(lista_stato)
            str_motivazioni = "\n".join(lista_motivazioni)
            
            # C. Lista esclusione per Cypher (evita allucinazioni su dati già noti)
            excluded_titles = nomi_visitati + nomi_futuri
            formatted_list = str(excluded_titles) 

            # D. Prompt Aggiornato con SEPARAZIONE DELLE INFORMAZIONI
            prompt_query = (
                f"L'opera davanti a cui ci troviamo ORA è: '{current_art_exact}'.\n\n"
                f"STATO DEL TOUR (Cosa abbiamo fatto e cosa faremo):\n{str_stato_tour}\n\n"
                f"MOTIVAZIONI PERCORSO (Perchè abbiamo scelto queste opere specificando che è legato agli interessi mostrati dal visitatore):\n{str_motivazioni}\n\n"
                f"Storico conversazione:\n{recent_history}\n"
                f"Domanda utente: {user_input}"
            )
            
            res = self.qa_chain.invoke({
                "query": prompt_query,
                "excluded_titles": formatted_list
            })
            
            self.chat_history.append(f"Sophia: {res['result']}")
            return UnityResponse(
                text=f"SOPHIA: {res['result']}", 
                robot_message=f"SOPHIA: {res['result']}"
            )
            
        except Exception as e:
            print(f"[DEBUG] Errore QA Chain o Intent: {e}")
            try:
                # Fallback in caso di errore
                res = self.qa_chain.invoke({
                    "query": user_input, 
                    "excluded_titles": "[]"
                })
                return UnityResponse(
                    text=f"SOPHIA: {res['result']}", 
                    robot_message=f"SOPHIA: {res['result']}"
                )
            except:
                return UnityResponse(
                    text="SOPHIA: Scusami, ho avuto un problema tecnico nel consultare il database. Prova a chiedermi qualcos'altro!",
                    robot_message="SOPHIA: Ho avuto un problema tecnico."
                )

# --- SERVER ---
bot_guida = RoboGuida()

@app.post("/chat")
async def chat_endpoint(msg: UnityMessage):
    return bot_guida.handle_unity_input(msg.text, msg.battery_level)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)