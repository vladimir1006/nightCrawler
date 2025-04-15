#!/usr/bin/python3
from requests_tor import RequestsTor 
import bs4
import os
import re
from utils import colors

### FONCTIONS DE REGEX 
def linksScraping(lasoupe : str, url : str) -> list:
    urlList = []
    urls = re.findall('http.?:\/\/[\w_-]+(?:(?:\.onion+)+)', str(lasoupe))
    #print(urls)
    print(url)
    
    for lien in urls:
        reggex = re.match(url,lien)
        print(lien)
        exit()
        if not bool(reggex):
            urlList.append(url)
        
    
    return urlList


def searchWordInText(keywords:list,text:str) -> list:
    liste = []
    for word in keywords:
        occ = re.findall(word,text.lower())
        if len(occ) != 0 :
            liste.append((word,len(occ)))
    return liste


### FONCTION DE FICHIER

def verifDirFile(dir,file) -> str:
    if os.path.isdir(dir) == False:
        raise ValueError("## Directory {} does not exist.\nProgramm abort. ##".format(file))
    
    if os.path.isfile("{}/{}.html".format(dir,file)) == False:
        os.system("touch {}/{}.html".format(dir,file))
    return "{}/{}.html".format(dir,file)

def webUrlFromFile(file:str) -> list: # faire avec un .json
    liste = []
    if os.path.isfile(file) == False:
        raise ValueError(" ## File {} does not exist.\nProgramm abort. ##".format(file))
    with open(file,'r') as f:
        for row in f:
            tmp = truncCR(row)
            tmp = tmp.split(' ')
            tuple = (tmp[0],tmp[1])
            liste.append(tuple)
    return liste

def keywordsFromFile(file):
    liste = []
    if os.path.isfile(file) == False:
        raise ValueError(" ## File {} does not exist.\nProgramm abort. ##".format(file))
    with open(file,"r") as f:
        for row in f:
            liste.append(truncCR(row))
    return liste

def raiseFileError(nameDirectory):
    raise ValueError("\n ## Impossible to open the file or directory : {} doesn't exist. ##".format(nameDirectory))

def isUrlinFile(file,url) -> bool:
        with open(file,"r") as f:
            if url in f.readline():
                return True
        return False


### FONCTION DE CHEPAQUOI

def truncCR(row):
    tmp = row.removesuffix('\n')
    return tmp

def isUrlOK(url): # a revoir
    reponse = requests.get(url)
    if(str(reponse) == "<Response [200]>"):
        print("\n" + colors.colors.BOLD + colors.colors.OKGREEN + f"{reponse}" + colors.colors.ENDC + f" : {url}")
    else:
        print("\n" + colors.colors.BOLD + colors.colors.WARNING + f"{reponse}" + colors.colors.ENDC + f" : {url}")

def downloadHtmlContent(nameDirectory, url, textVariable):
    if os.path.isdir(nameDirectory) == False:
        raiseFileError(nameDirectory)
    path = verifDirFile(nameDirectory,url)
    with open(path,'w') as f:
        f.write(str(textVariable))



### FONCTION DE SCRAPER

def scarpe_search(url:str ,name_url:str,keywords:list, isUrlContentDownload :bool):
    try:
        reponse = requests.get(url) 
    except:
        raise ValueError(colors.colors.FAIL +"\n ## {} can't be reached. ##".format(name_url))
    lasoupe = bs4.BeautifulSoup(reponse.text,'html.parser') 

    if isUrlContentDownload:
        downloadHtmlContent('webPages',name_url,lasoupe)

    text_content = lasoupe.get_text()
    searchWords = searchWordInText(keywords,text_content) 
    urlFound = linksScraping(lasoupe,url) # search link url .onion
    print(colors.colors.BOLD + colors.colors.OKBLUE + " urls founds : {}".format(len(urlFound)))
    
    if len(searchWords) == 0: 
        raise ValueError(colors.colors.BOLD + colors.colors.WARNING +"\n ## No keywords found. ##") #mettre en rouge
    for i in  searchWords:
        print(colors.colors.BOLD + colors.colors.OKBLUE + " {} : {}".format(i[0],i[1])+ colors.colors.ENDC)
    return (urlFound,searchWords)


if __name__=="__main__":
    sitekeyword = []
    try:
        # tor ouvre un port automatiquement sur le port 9051
        requests = RequestsTor(tor_ports=(9050,),tor_cport=9051, password="jesuisdanslacuisinetubouffescequejeteprepare") # add a password


        # site = webUrlFromFile("url.txt") 
        # print(site)
        # print(requests.get("http://p53lf57qovyuvwsc6xnrppyply3vtqm7l6pcobkmyqsiofyeznfu5uqd.onion/").text)
        # exit()

        site = webUrlFromFile("url.txt") # faire avec un fichier json dans un tableau
        print(site)
        keywords = keywordsFromFile("keywords.txt")
        print(keywords)
        #exit()

        for name,url in site:
            print(colors.colors.BOLD + colors.colors.OKCYAN +f"\n -- Scraping {name} : {url} --" + colors.colors.ENDC)
            try :
                sitekeyword.append(scarpe_search(url,name,keywords,False))
                
            except Exception as error:
                print(error)
                continue
        print(sitekeyword[1])
        print(sitekeyword[-1])
    except Exception as error:
        print(error)