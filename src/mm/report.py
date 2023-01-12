from pydantic import BaseModel, Field
from src.mm.models.deployment import Deployment, DeploymentResponse
from src.mm.models.affectedProduct import AffectedProduct, AffectedProductResponse
from src.mm.models.vulnerability import Vulnerability, VulnerabilityResponse
from bs4 import BeautifulSoup as bs
import aiohttp
import asyncio
import calendar
import re


def unpack_office_kbs(rep: 'MonthlyReport'):
    pass


def unpack_misc_kbs(rep: 'MonthlyReport'):
    doc = bs(rep.misc_html, 'html.parser')
    sections = doc.find('article').find_all('section', class_='ocpSection')
    for section in sections:
        if section.h2:
            if section.h2.get_text() == "More Information":
                month = section.section.h3.get_text()
                if month == rep.patch_day:
                    ul = section.section.find_all('ul', recursive=False)
                    for kb in ul:
                        kbtitle = re.search(r"\d*-\d* ([\S\s]*)[(]KB(\d*)[)]", kb.li.p.b.string)
                        if kbtitle:
                            num = kbtitle.group(2)
                        if num in rep.unique_kb:
                            continue
                        else:
                            new_kb = Kb(kb=num)
                            new_kb.releaseDate = get_second_tuesday_date(rep.year, rep.month)
                            new_kb.url = f'https://support.microsoft.com/help/{num}'
                            rep.kbs.append(new_kb)



def unpack_data(rep: 'MonthlyReport'):
    for d in rep.deployments:
        if re.search(r'.* .*', d.articleName):
            continue
        elif d.articleName not in rep.unique_kb:
            kb = Kb(kb=d.articleName, url=d.articleUrl, releaseDate=d.releaseDate[:10])
            kb.severity.append(d.severity)
            rep.kbs.append(kb)
            rep.unique_kb.append(d.articleName)
        else:
            kb = [x for x in rep.kbs if x.kb == d.articleName][0]
            kb.severity.append(d.severity)
    unpack_misc_kbs(rep)
    unpack_office_kbs(rep)


def get_specific_deployment_by_article(articleName: str):
    return f"https://api.msrc.microsoft.com/sug/v2.0/en-US/deployment/?%24orderBy=product+desc&%24filter=articleName+eq+%27{articleName}%27"


def get_specific_ap_by_cve(cveNumber: str):
    return f"https://api.msrc.microsoft.com/sug/v2.0/en-US/affectedProduct?%24filter=cveNumber+eq+%27{cveNumber}%27"


def get_specific_ap_by_id(ap_id: str):
    return f"https://api.msrc.microsoft.com/sug/v2.0/en-US/affectedProduct/{ap_id}"


def get_specific_vuln_by_cve(cveNumber: str):
    return f"https://api.msrc.microsoft.com/sug/v2.0/en-US/vulnerability?%24orderBy=cveNumber+desc&%24filter=cveNumber+eq+%27{cveNumber}%27"


def get_misc_url() -> str:
    return "https://support.microsoft.com/help/894199"


def get_catalog_url(articleName: str) -> str:
    # include "KB" letters in the search
    return f"https://www.catalog.update.microsoft.com/Search.aspx?q=KB{articleName}"


def get_catalog_inline_url(update_id: str) -> str:
    # "https://www.catalog.update.microsoft.com/ScopedViewInline.aspx?updateid=" + id + "#PackageDetails"
    return f"https://www.catalog.update.microsoft.com/ScopedViewInline.aspx?updateid={update_id}#PackageDetails"


async def gather_deployment(session: aiohttp.ClientSession, rep: 'MonthlyReport'):
    url = rep.get_deployment_api_url(skip=0)
    async with session.get(url) as response:
        json = await response.json()
        resp = DeploymentResponse(**json)
        resp_list = resp.value
        if int(resp.count) > len(resp_list):
            pages = [x*len(resp.value) for x in [*range(1, (int(resp.count)//len(resp.value) + 1))]]
            for page in pages:
                async with session.get(rep.get_deployment_api_url(skip=page)) as p:
                    json = await p.json()
                    resp_list.extend(DeploymentResponse(**json).value)
        rep.deployments = resp_list


async def gather_ap(session: aiohttp.ClientSession, rep: 'MonthlyReport'):
    url = rep.get_affectedProduct_api_url(skip=0)
    async with session.get(url) as response:
        json = await response.json()
        resp = AffectedProductResponse(**json)
        resp_list = resp.value
        if int(resp.count) > len(resp_list):
            pages = [x*len(resp.value) for x in [*range(1, (int(resp.count)//len(resp.value) + 1))]]
            for page in pages:
                async with session.get(rep.get_affectedProduct_api_url(skip=page)) as p:
                    json = await p.json()
                    resp_list.extend(AffectedProductResponse(**json).value)
        rep.aps = resp_list


async def gather_vulnerability(session: aiohttp.ClientSession, rep: 'MonthlyReport'):
    url = rep.get_vulnerability_api_url(skip=0)
    async with session.get(url) as response:
        json = await response.json()
        resp = VulnerabilityResponse(**json)
        resp_list = resp.value
        if int(resp.count) > len(resp_list):
            pages = [x*len(resp.value) for x in [*range(1, (int(resp.count)//len(resp.value) + 1))]]
            for page in pages:
                async with session.get(rep.get_vulnerability_api_url(skip=page)) as p:
                    json = await p.json()
                    resp_list.extend(VulnerabilityResponse(**json).value)
        rep.vulnerabilities = resp_list


async def gather_misc(session: aiohttp.ClientSession, rep: 'MonthlyReport'):
    url = get_misc_url()
    async with session.get(url) as response:
        rep.misc_html = await response.text()


async def gather_office(session: aiohttp.ClientSession, rep: 'MonthlyReport'):
    url = rep.get_office_url()
    async with session.get(url) as response:
        rep.office_html = await response.text()


async def gather_data(report: 'MonthlyReport'):
    async with aiohttp.ClientSession() as session:
        tasks = [asyncio.create_task(gather_deployment(session, report)),
                 asyncio.create_task(gather_ap(session, report)),
                 asyncio.create_task(gather_vulnerability(session, report)),
                 asyncio.create_task(gather_misc(session, report)),
                 asyncio.create_task(gather_office(session, report))]
        await asyncio.gather(*tasks)


def get_second_tuesday_string(y: int, m: int) -> str:
    """Get a string representation for the chosen second Tuesday

    :param y: year
    :param m: month
    :return: string in form -> 'Tuesday, Month dd, yyyy'
    """
    c = calendar.Calendar()
    # d[3] == 1 means the 4th value in the d tuple should match 1, which is Tuesday
    # d[1] == month means the second value in the d tuple should match month
    # ending [1] means the second value in the returned list, which is the second Tuesday date tuple
    second = list(filter(lambda d: d[3] == 1 and d[1] == m, c.itermonthdays4(y, m)))[1]
    months = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June", 7: "July", 8: "August",
              9: "September", 10: "October", 11: "November", 12: "December"}
    return f"Tuesday, {months[m]} {second[2]}, {y}"


def get_second_tuesday_date(y: int, m: int, delta: int = 0) -> str:
    """Get a string representation for the chosen second Tuesday

    :param y: year
    :param m: month
    :param delta: days before(-) or after(+) the second tuesday
    :return: string in form -> 'yyyy-mm-dd'
    """
    c = calendar.Calendar()
    # d[3] == 1 means the 4th value in the d tuple should match 1, which is Tuesday
    # d[1] == month means the second value in the d tuple should match month
    # ending [1] means the second value in the returned list, which is the second Tuesday date tuple
    second = list(filter(lambda d: d[3] == 1 and d[1] == m, c.itermonthdays4(y, m)))[1]
    return f"{second[0]}-{second[1]:02d}-{second[2]+delta:02d}"


class Kb(BaseModel):
    kb: str
    url: str = ""
    title: str = ""
    releaseDate: str = ""
    products: list[str] = Field(default_factory=list)
    severity: list[str] = Field(default_factory=list)
    description: str = ""

    def highest_severity(self) -> str:
        if "Critical" in self.severity:
            return "Critical"
        elif "Important" in self.severity:
            return "Important"
        elif "Moderate" in self.severity:
            return "Moderate"
        else:
            return "N/A"


class MonthlyReport(BaseModel):
    name: str
    year: int
    month: int
    misc_html: str = ""
    office_html: str = ""
    deployments: list[Deployment] = Field(default_factory=list)
    aps: list[AffectedProduct] = Field(default_factory=list)
    vulnerabilities: list[Vulnerability] = Field(default_factory=list)
    kbs: list[Kb] = Field(default_factory=list)
    unique_kb: list[str] = Field(default_factory=list)
    msrc_updates: int = Field(default=0)
    misc_updates: int = Field(default=0)
    office_updates: int = Field(default=0)
    msrc_cve: int = Field(default=0)

    @property
    def patch_day(self) -> str:
        return get_second_tuesday_string(self.year, self.month)

    @property
    def start(self):
        if self.month == 1:
            month = 12
            year = self.year - 1
        else:
            month = self.month
            year = self.year
        return f"{get_second_tuesday_date(year, month, 1)}"

    @property
    def start_encoded(self):
        return f"{self.start}T00%3A00%3A00-06%3A00"

    @property
    def end(self):
        return f"{get_second_tuesday_date(self.year, self.month, 2)}"

    @property
    def end_encoded(self):
        return f"{self.end}T23%3A59%3A59-06%3A00"

    def get_vulnerability_api_url(self, skip: int) -> str:
        return f"https://api.msrc.microsoft.com/sug/v2.0/en-US/vulnerability?%24orderBy=cveNumber+asc&%24filter=%28releaseDate+gt+{self.start}T00%3A00%3A00-05%3A00+or+latestRevisionDate+gt+{self.start}T00%3A00%3A00-05%3A00%29+and+%28releaseDate+lt+{self.end}T23%3A59%3A59-05%3A00+or+latestRevisionDate+lt+{self.end}T23%3A59%3A59-05%3A00%29&$skip={str(skip)}"

    def get_affectedProduct_api_url(self, skip: int) -> str:
        return f"https://api.msrc.microsoft.com/sug/v2.0/en-US/affectedProduct?%24orderBy=releaseDate+desc&%24filter=%28releaseDate+gt+{self.start}T00%3A00%3A00-05%3A00%29+and+%28releaseDate+lt+{self.end}T23%3A59%3A59-05%3A00%29&$skip={str(skip)}"

    def get_deployment_api_url(self, skip: int) -> str:
        return f"https://api.msrc.microsoft.com/sug/v2.0/en-US/deployment/?%24orderBy=product+desc&%24filter=%28releaseDate+gt+{self.start}T00%3A00%3A00-06%3A00%29+and+%28releaseDate+lt+{self.end}T23%3A59%3A59-06%3A00%29&$skip={str(skip)}"

    def get_office_url(self) -> str:
        # "https://docs.microsoft.com/en-us/officeupdates/office-updates-msi"
        y = int(self.end[0:4])
        m = int(self.end[5:7])
        kb_dict = {
            1: "5002084",
            2: "5002085",
            3: "5002086",
            4: "5002087",
            5: "5002088",
            6: "5002089",
            7: "5002090",
            8: "5002091",
            9: "5002092",
            10: "5002093",
            11: "5002094",
            12: "5002095",
        }
        base_url = "https://support.microsoft.com/help/"
        if y == 2023:
            if 13 > m > 0:
                return f'{base_url}{kb_dict[m]}'
        else:
            print('Not the right year.')
            return ""

    async def run(self):
        print(f"Starting: {self.name}")
        print(f"Patch Tuesday: {self.patch_day}")
        print("Report Range:")
        print(self.start, "to", self.end)
        await gather_data(self)
        unpack_data(self)
