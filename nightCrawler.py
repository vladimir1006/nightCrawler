#!/bin/python3 
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

# Constantes
TOR_PROXY = "socks5://127.0.0.1:9050"
USER_AGENT = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
]


"""
    TODO:
        -parse words in html content and count on each page
        -file report: one file shared with workers and incresing counter with url annotation
            --- ciadotgov....onion ---- 
            # access this part and increment the counter and release the lock
            # if there isn't dedicated section, append to the end of the file 
                word1 : 14
                word2 : ...

        -do some math shit to make a words "score" 
        -apply ratio to words for the level of importance of the word. 
        -do some math shit again
        -faire tests
"""

class Utils:
    def __init__(self):
        pass
    @staticmethod
    def retrieve_words_file(filename: str):
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
        except:
            logging.error(msg=f"Impossible to read the file {filename}")
            raise Exception()


class Parser:
    
    @staticmethod
    def get_links(content: str, base_url: str) -> tuple[list[str], list[str]]:
        """
            Extract the links from a HTML page content.
            Args: 
                content (str): HTML page content.
                base_url (str): The url of the page to retrieve the internals links.
            Returns:
                tuple: (internals_links, externals_links)
        """
        dom = HTMLParser(content)
        base = urlparse(base_url)
        domain = base.netloc
        locals, externals = [], []
        
        for a in dom.css('a'):
            href = a.attrs.get("href")
            if not href:
                continue
            full_url = urljoin(base_url, href)
            full_url = re.sub(r'#.*$', '', full_url)
            full_url = re.sub(r'\?.*$', '', full_url)
            url = urlparse(full_url)
            if bool(url.netloc) and url.netloc == domain and url.scheme in ('http', 'https'):
                locals.append(full_url)
            else:
                externals.append(full_url)
            
        return locals, externals

    @staticmethod
    def get_words(content:str, words: set) -> dict:
        # faire des ressemblances de mots: plural, accords, ...
        words = {word.lower() for word in words}
        content = content.lower()
        _words = re.findall(r"\w+", content)
        cpt = Counter(_words)
        return {m: cpt[m] for m in words} 

class TorRequester:
    def __init__(self, proxy=TOR_PROXY, concurrency_limit=10) -> None:
        self.proxy = proxy 
        self.concurrency_limit = concurrency_limit
        self.semaphore = None
        self.session = None
        # Random headers
        self.headers = {'User-Agent': random.choice(USER_AGENT)}
        self.results = {}
    
    async def __aenter__(self):
        """
            Deal with the async context.
        """
        connector = ProxyConnector.from_url(self.proxy)
        self.session = aiohttp.ClientSession(connector=connector)
        self.semaphore = asyncio.Semaphore(self.concurrency_limit)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
            Close the session at the end.
        """
        if self.session:
            await self.session.close()
    
    
    async def fetch(self, url):
        """
            Fetch the content of a URL.
            Args:
                url (str): URL to fetch.
            Returns:
                tuple: (content, status) or None if error.
        """
        async with self.semaphore:
            try:
                async with self.session.get(url, headers=self.headers, timeout=30) as response:
                    if response.status == 200:
                        content = await response.text()
                        print(f"✅ {url} - Statut: {response.status} - {content[:15]}")
                        return content, response.status
                    else:
                        print(f"{url} - Statut: {response.status}")
                        return None, response.status
            except Exception as e:
                print(f"{url} - Erreur: {e}")
                return None, None


class SharedDict:
    def __init__(self, initialState) -> None:
        self.data = initialState
        self.lock = asyncio.Lock()

    async def increment_dict(self, words: dict[str, int]):
        print(words)
        async with self.lock:
            for k in words:
                self.data[k] += 1
            return self.data
    async def get_values(self):
        return self.data

class AsyncCrawler:
    def __init__(self, start_url: str, words: set,concurrency_limit=10):
        """
        Initialise le crawler asynchrone avec séparation des responsabilités.
        
        Args:
            start_url: L'URL de départ pour le crawling
            use_tor: Utiliser ou non Tor pour les requêtes
            concurrency_limit: Nombre maximum de requêtes concurrentes
        """
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
        """Initialise la file d'attente avec l'URL de départ"""
        await self.urls_to_visit.put(self.start_url)
        print("FAIRE LOGS")
    
    def _url_refactor(self,url):
        return 'http://'+url if urlparse(url).scheme == '' else url

    async def _check_value_queue(self,queue, value):
        """
            Check if a value is in the queue.

            Returns: 
                bool: True if the element is in queue, False otherwise
        """
        _queue = queue
        while not _queue.empty():
            item = await _queue.get()
            if item == value:
                return True
        return False

    async def process_url(self):
        """
        Traite une URL de la file d'attente.
        
        Returns:
            bool: True si une URL a été traitée, False si la queue est vide
        """
        try:
            url = await asyncio.wait_for(self.urls_to_visit.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return False
        print(self.urls_to_visit)
        # CHANGE THAT SO IT WORKS
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
        """Worker qui traite continuellement des URLs jusqu'à ce que la queue soit vide"""
        while True:
            if self.urls_to_visit.empty() and len(self.visited_urls) > 0:
                await asyncio.sleep(1.0)
                if self.urls_to_visit.empty():
                    break
            
            if not await self.process_url():
                await asyncio.sleep(0.1)
    
    async def crawl(self):
        """
        Démarre le processus de crawling.
        
        Returns:
            dict: Résultats du crawling (URLs visitées, liens internes, liens externes)
        """ 
        proxy = TOR_PROXY
            
        await self.initialise()
        async with TorRequester(proxy=proxy, concurrency_limit=self.concurrency_limit) as requester:
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


async def main(start_url: str, wordsFilename: str):
    """
    Fonction principale qui démarre le crawling.
    
    Args:
        start_url: L'URL de départ pour le crawling
        use_tor: Utiliser ou non Tor pour les requêtes
    """
    print(f"Démarrage du crawling de {start_url}")
    
    try : 
        utils = Utils()
        words = utils.retrieve_words_file(wordsFilename)
    except:
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
    
    parser = argparse.ArgumentParser(description="Crawler asynchrone sur Tor")
    parser.add_argument("url", help="L'URL de départ pour le crawling")
    parser.add_argument("words", help="File containing the words to scrape")
    
    args = parser.parse_args()
    
    asyncio.run(main(args.url, args.words))

