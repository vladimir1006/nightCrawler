#!/bin/python3
"""
tor_crawler.py — Crawler OSINT asynchrone via Tor

Parcourt récursivement les pages internes d'un site .onion ou clearnet,
extrait et compte la fréquence de mots-clés ciblés sur chaque page visitée.
Les requêtes sont anonymisées via le réseau Tor (proxy SOCKS5 local).

Usage:
    python3 tor_crawler.py <url> <fichier_mots>

Arguments:
    url           URL de départ (ex: http://example.onion)
    fichier_mots  Fichier texte contenant un mot-clé par ligne

Prérequis:
    - Service Tor en écoute sur 127.0.0.1:9050
    - pip install aiohttp aiohttp-socks selectolax
"""

import asyncio
from os.path import isfile
import random
import re
from urllib.parse import urljoin, urlparse
import aiohttp
from aiohttp_socks import ProxyConnector
from selectolax.parser import HTMLParser
from collections import Counter
import os
import logging

# --- Constantes ---

TOR_PROXY = "socks5://127.0.0.1:9050"
"""Adresse du proxy SOCKS5 Tor local."""

USER_AGENT = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
]
"""Pool de User-Agents utilisés aléatoirement pour réduire la détection."""


# TODO (améliorations prévues):
#   - Comptage des mots par page avec annotation de l'URL source dans le rapport
#   - Génération d'un score pondéré par mot (ratio importance/fréquence)
#   - Export du rapport dans un fichier partagé entre workers avec verrou
#   - Tests unitaires sur Parser et SharedDict


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

class Utils:
    """Utilitaires statiques pour le chargement des ressources."""

    @staticmethod
    def retrieve_words_file(filename: str) -> dict:
        """
        Charge un fichier de mots-clés et retourne un dictionnaire {mot: 0}.

        Chaque ligne du fichier correspond à un mot-clé (insensible à la casse).
        Le dictionnaire résultant initialise les compteurs à zéro.

        Args:
            filename (str): Chemin vers le fichier texte de mots-clés.

        Returns:
            dict: Dictionnaire {mot_clé_lowercase: 0}.

        Raises:
            SystemExit: Si le fichier est introuvable ou illisible.
        """
        try:
            if os.path.isfile(filename):
                _dict = dict()
                with open(filename, "r") as f:
                    for line in f.readlines():
                        _dict[line.strip().lower()] = 0
                return _dict
        except FileNotFoundError:
            logging.exception(msg=f"The file: {filename} doesn't not exists")
            exit(1)
        except Exception:
            logging.error(msg=f"Impossible to read the file {filename}")
            raise Exception()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class Parser:
    """
    Analyse statique du contenu HTML.

    Responsabilités :
        - Extraction des liens internes et externes d'une page.
        - Comptage des occurrences de mots-clés dans le contenu textuel.
    """

    @staticmethod
    def get_links(content: str, base_url: str) -> tuple[list[str], list[str]]:
        """
        Extrait et catégorise les liens présents dans une page HTML.

        Les fragments (#ancre) et les paramètres de requête (?param=val) sont
        supprimés pour éviter les doublons lors du crawl.

        Args:
            content (str):  Contenu HTML brut de la page.
            base_url (str): URL de la page courante, utilisée pour résoudre
                            les liens relatifs et identifier le domaine interne.

        Returns:
            tuple[list[str], list[str]]: (liens_internes, liens_externes)
                - liens_internes : même domaine que base_url, schéma http/https.
                - liens_externes : tout autre lien (autre domaine, mailto, etc.).
        """
        dom = HTMLParser(content)
        base = urlparse(base_url)
        domain = base.netloc
        locals, externals = [], []

        for a in dom.css('a'):
            href = a.attrs.get("href")
            if not href:
                continue
            # Résolution des liens relatifs
            full_url = urljoin(base_url, href)
            # Suppression des fragments et query strings
            full_url = re.sub(r'#.*$', '', full_url)
            full_url = re.sub(r'\?.*$', '', full_url)
            url = urlparse(full_url)
            if bool(url.netloc) and url.netloc == domain and url.scheme in ('http', 'https'):
                locals.append(full_url)
            else:
                externals.append(full_url)

        return locals, externals

    @staticmethod
    def get_words(content: str, words: set) -> dict:
        """
        Compte les occurrences de chaque mot-clé dans le contenu textuel.

        La comparaison est insensible à la casse. Seuls les mots alphanumériques
        sont extraits (ponctuation ignorée).

        Args:
            content (str): Contenu textuel (HTML ou texte brut) à analyser.
            words (set):   Ensemble de mots-clés à rechercher.

        Returns:
            dict: {mot_clé: nombre_d_occurrences} pour chaque mot de `words`.

        Note:
            Les variantes morphologiques (pluriels, accords) ne sont pas gérées.
            Une normalisation par stemming/lemmatisation est envisagée (TODO).
        """
        words = {word.lower() for word in words}
        content = content.lower()
        _words = re.findall(r"\w+", content)
        cpt = Counter(_words)
        return {m: cpt[m] for m in words}


# ---------------------------------------------------------------------------
# TorRequester
# ---------------------------------------------------------------------------

class TorRequester:
    """
    Client HTTP asynchrone tunnelisant les requêtes via Tor (SOCKS5).

    Conçu comme context manager asynchrone pour garantir la fermeture
    propre de la session aiohttp.

    Attributes:
        proxy (str):             URL du proxy SOCKS5 (défaut: 127.0.0.1:9050).
        concurrency_limit (int): Nombre maximum de requêtes simultanées.
        semaphore (Semaphore):   Contrôle de la concurrence (initialisé à l'entrée).
        session (ClientSession): Session aiohttp partagée entre les workers.
        headers (dict):          En-têtes HTTP avec User-Agent aléatoire.
    """

    def __init__(self, proxy=TOR_PROXY, concurrency_limit=10) -> None:
        self.proxy = proxy
        self.concurrency_limit = concurrency_limit
        self.semaphore = None
        self.session = None
        self.headers = {'User-Agent': random.choice(USER_AGENT)}
        self.results = {}

    async def __aenter__(self):
        """Initialise la session aiohttp avec le connecteur Tor et le sémaphore."""
        connector = ProxyConnector.from_url(self.proxy)
        self.session = aiohttp.ClientSession(connector=connector)
        self.semaphore = asyncio.Semaphore(self.concurrency_limit)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Ferme proprement la session aiohttp à la sortie du context manager."""
        if self.session:
            await self.session.close()

    async def fetch(self, url: str) -> tuple[str | None, int | None]:
        """
        Effectue une requête GET sur l'URL donnée via Tor.

        L'accès concurrent est limité par le sémaphore de l'instance.
        Timeout fixé à 30 secondes (adapté aux latences du réseau Tor).

        Args:
            url (str): URL cible.

        Returns:
            tuple[str | None, int | None]:
                - (contenu_html, code_statut) si la requête réussit (HTTP 200).
                - (None, code_statut) si le statut est != 200.
                - (None, None) en cas d'exception réseau.
        """
        async with self.semaphore:
            try:
                async with self.session.get(url, headers=self.headers, timeout=30) as response:
                    if response.status == 200:
                        content = await response.text()
                        print(f"{url} - Statut: {response.status} - {content[:15]}")
                        return content, response.status
                    else:
                        print(f"{url} - Statut: {response.status}")
                        return None, response.status
            except Exception as e:
                print(f"{url} - Erreur: {e}")
                return None, None


# ---------------------------------------------------------------------------
# SharedDict
# ---------------------------------------------------------------------------

class SharedDict:
    """
    Dictionnaire partagé thread-safe pour l'agrégation des compteurs de mots.

    Utilise un verrou asyncio pour garantir l'atomicité des incréments
    lorsque plusieurs workers accèdent simultanément aux données.

    Attributes:
        data (dict): Dictionnaire {mot: compteur} partagé entre les workers.
        lock (Lock): Verrou asyncio protégeant les accès concurrents.
    """

    def __init__(self, initialState: dict) -> None:
        self.data = initialState
        self.lock = asyncio.Lock()

    async def increment_dict(self, words: dict[str, int]) -> dict:
        """
        Incrémente les compteurs du dictionnaire partagé de façon atomique.

        Args:
            words (dict[str, int]): Occurrences trouvées sur une page
                                    {mot: nb_occurrences}.

        Returns:
            dict: État courant du dictionnaire après mise à jour.
        """
        print(words)
        async with self.lock:
            for k in words:
                self.data[k] += 1
            return self.data

    async def get_values(self) -> dict:
        """Retourne l'état courant du dictionnaire de compteurs."""
        return self.data


# ---------------------------------------------------------------------------
# AsyncCrawler
# ---------------------------------------------------------------------------

class AsyncCrawler:
    """
    Crawler web asynchrone multi-workers avec gestion de la file d'URLs.

    Architecture :
        - Une file asyncio.Queue stocke les URLs à visiter.
        - N workers (tâches asyncio) consomment la file en parallèle.
        - Un SharedDict agrège les compteurs de mots de façon thread-safe.
        - Un TorRequester centralise les requêtes HTTP anonymisées.

    Attributes:
        start_url (str):          URL de départ du crawl.
        base_domain (str):        Domaine extrait de start_url (filtre interne).
        visited_urls (set):       URLs déjà visitées (évite les doublons).
        urls_to_visit (Queue):    File des URLs en attente de traitement.
        concurrency_limit (int):  Nombre de workers simultanés.
        requester (TorRequester): Instance du client HTTP Tor.
        parser (Parser):          Instance du parser HTML statique.
        internal_links (set):     Tous les liens internes découverts.
        external_links (set):     Tous les liens externes découverts.
        words (set):              Mots-clés à rechercher.
        dict (SharedDict):        Compteurs agrégés de mots-clés.
    """

    def __init__(self, start_url: str, words: set, concurrency_limit=10):
        self.start_url = start_url
        self.base_domain = urlparse(start_url).netloc
        self.visited_urls = set()
        self.urls_to_visit = asyncio.Queue()
        self.concurrency_limit = concurrency_limit
        self.requester = None
        self.parser = Parser()
        self.internal_links = set()
        self.external_links = set()
        self.words = words
        self.dict = SharedDict({word: 0 for word in self.words})

    async def initialise(self):
        """Amorce la file d'URLs avec l'URL de départ."""
        await self.urls_to_visit.put(self.start_url)
        print("FAIRE LOGS")

    def _url_refactor(self, url: str) -> str:
        """
        Normalise une URL en ajoutant le schéma 'http://' si absent.

        Args:
            url (str): URL potentiellement sans schéma.

        Returns:
            str: URL avec schéma garanti.
        """
        return 'http://' + url if urlparse(url).scheme == '' else url

    async def _check_value_queue(self, queue: asyncio.Queue, value) -> bool:
        """
        Vérifie si une valeur est présente dans la queue en la vidant.

        ATTENTION : cette méthode consomme la queue — les éléments lus
        ne sont pas réinsérés. Réservée à un usage de débogage.

        Args:
            queue (asyncio.Queue): File à inspecter.
            value:                 Valeur recherchée.

        Returns:
            bool: True si la valeur est trouvée, False sinon.
        """
        _queue = queue
        while not _queue.empty():
            item = await _queue.get()
            if item == value:
                return True
        return False

    async def process_url(self) -> bool:
        """
        Dépile et traite une URL depuis la file d'attente.

        Workflow pour chaque URL :
            1. Dépilage avec timeout de 1s (évite le blocage indéfini).
            2. Vérification contre les URLs déjà visitées.
            3. Récupération du contenu HTML via TorRequester.
            4. Extraction des liens internes (ajoutés à la file) et externes.
            5. Comptage des mots-clés et mise à jour du SharedDict.

        Returns:
            bool: True si une URL a été traitée ou dépilée,
                  False si la file est vide (timeout atteint).
        """
        try:
            url = await asyncio.wait_for(self.urls_to_visit.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return False

        print(self.urls_to_visit)

        if url in self.visited_urls:
            self.urls_to_visit.task_done()
            return True

        url = self._url_refactor(url)
        self.visited_urls.add(url)

        content, status = await self.requester.fetch(url)

        if content:
            internal_links, external_links = self.parser.get_links(content, url)
            _dict = self.parser.get_words(content, self.words)
            await self.dict.increment_dict(_dict)

            self.internal_links.update(internal_links)
            self.external_links.update(external_links)

            for link in internal_links:
                if link not in self.visited_urls:
                    print(f"{link} add to list")
                    await self.urls_to_visit.put(link)

        self.urls_to_visit.task_done()
        return True

    async def worker(self):
        """
        Worker asynchrone traitant les URLs en continu.

        Stratégie d'arrêt :
            - Si la file est vide ET qu'au moins une URL a été visitée,
              le worker attend 1 seconde puis vérifie à nouveau.
            - Si la file est toujours vide après cette attente, le worker
              se termine (condition de fin du crawl).
        """
        while True:
            if self.urls_to_visit.empty() and len(self.visited_urls) > 0:
                await asyncio.sleep(1.0)
                if self.urls_to_visit.empty():
                    break
            if not await self.process_url():
                await asyncio.sleep(0.1)

    async def crawl(self) -> dict:
        """
        Point d'entrée principal du crawl.

        Lance 20 workers concurrents partageant un unique TorRequester,
        attend leur complétion, puis retourne les résultats agrégés.

        Returns:
            dict: {
                "visited_urls"  : set des URLs visitées,
                "internal_links": set de tous les liens internes découverts,
                "external_links": set de tous les liens externes découverts,
            }
        """
        await self.initialise()
        async with TorRequester(proxy=TOR_PROXY, concurrency_limit=self.concurrency_limit) as requester:
            self.requester = requester
            workers = [
                asyncio.create_task(self.worker())
                for _ in range(20)
            ]
            await asyncio.gather(*workers)

        _dict = await self.dict.get_values()
        print(_dict)
        return {
            "visited_urls": self.visited_urls,
            "internal_links": self.internal_links,
            "external_links": self.external_links
        }


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

async def main(start_url: str, wordsFilename: str):
    """
    Fonction principale : charge les mots-clés, démarre le crawl et affiche
    le résumé des résultats.

    Args:
        start_url (str):     URL de départ pour le crawling.
        wordsFilename (str): Chemin vers le fichier de mots-clés.
    """
    print(f"Démarrage du crawling de {start_url}")

    try:
        utils = Utils()
        words = utils.retrieve_words_file(wordsFilename)
    except Exception:
        logging.error(f"Cannot retrieve the words in {wordsFilename}")
        exit(1)

    crawler = AsyncCrawler(start_url, words)
    results = await crawler.crawl()

    print(f"\nCrawling terminé. {len(results['visited_urls'])} URLs ont été visitées.")
    print(f"Liens internes trouvés: {len(results['internal_links'])}")
    print(f"Liens externes trouvés: {len(results['external_links'])}")

    print("\nListe des URLs visitées:")
    for url in sorted(results['visited_urls']):
        print(url)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Crawler asynchrone OSINT sur Tor")
    parser.add_argument("url", help="URL de départ pour le crawling")
    parser.add_argument("words", help="Fichier contenant les mots-clés à rechercher (un par ligne)")

    args = parser.parse_args()

    asyncio.run(main(args.url, args.words))
    
