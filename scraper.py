from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
import logging
import asyncio
from typing import Dict, List, Optional, Any
import re
from urllib.parse import quote

logger = logging.getLogger(__name__)

class MyDramaListScraper:
    def __init__(self):
        self.base_url = "https://mydramalist.com"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        self._session = None

    async def _get_session(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(impersonate="chrome110")
        return self._session

    # MyDramaList serves every image at several sizes, encoded as a one-letter
    # suffix on the filename: `_3t` thumb, `_3s` small, `_3m` medium, `_3c`
    # cropped, `_3f` full. Only the full variant is worth downloading once a
    # user has picked a photo, so normalise any variant to `f` here rather than
    # sprinkling the same regex through the callers. A missing/odd filename is
    # returned untouched — better a working medium image than a broken URL.
    @staticmethod
    def _full_size_image(url: str) -> str:
        if not url:
            return ''
        url = url.split('?', 1)[0]  # drop cache-busting query (`?v=1`)
        return re.sub(r'_(\d+)[a-z]?(\.[A-Za-z0-9]+)$', r'_\1f\2', url)

    async def _make_request(self, url: str) -> BeautifulSoup:
        """Make HTTP request and return BeautifulSoup object"""
        try:
            session = await self._get_session()
            response = await session.get(url, timeout=10)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'html.parser')
        except Exception as e:
            logger.error(f"Request failed for {url}: {str(e)}")
            raise

    async def search_dramas(self, query: str) -> Dict[str, Any]:
        """Search for dramas by query"""
        search_url = f"{self.base_url}/search?q={quote(query)}"
        soup = await self._make_request(search_url)
        

        results = []
        drama_items = soup.find_all('div', class_='box')
        
        for item in drama_items[:20]:
            try:
                title_elem = item.find('h6', class_='title')
                if not title_elem or not title_elem.find('a'):
                    continue
                
                title = title_elem.get_text(strip=True)
                link_elem = title_elem.find('a')
                link = link_elem['href'] if link_elem else ''
                slug = link.split('/')[-1] if link else ''
                
                if not slug or '/article/' in (link or ''):
                    continue

                year_elem = item.find('span', class_='text-muted')
                year_match = re.search(r'(\d{4})', year_elem.get_text(strip=True)) if year_elem else None
                year = year_match.group(1) if year_match else ''
                
                if not year:
                    continue

                img_elem = item.find('img', class_='lazy')
                image = img_elem['data-src'] if img_elem and 'data-src' in img_elem.attrs else (item.find('img')['src'] if item.find('img') else '')

                rating_elem = item.find('span', class_='score')
                rating = rating_elem.get_text(strip=True) if rating_elem else ''
                
                results.append({
                    'title': title,
                    'slug': slug,
                    'year': year,
                    'image': image,
                    'rating': rating,
                    'url': f"{self.base_url}{link}" if link else ''
                })
            except Exception as e:
                logger.error(f"Error parsing search result item: {str(e)}")
                continue

        return {"results": results, "total": len(results)}

    async def resolve_slug(self, query: str) -> Optional[str]:
        """Search and return the first drama slug if the input is a title"""
        results = await self.search_dramas(query)
        return results['results'][0]['slug'] if results['results'] else None

    async def get_drama_details(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get drama details by slug (or title)"""
        # If slug doesn't contain a digit-dash pattern, try to resolve it as a title
        if not re.search(r'\d+-', slug):
            resolved = await self.resolve_slug(slug)
            if resolved:
                slug = resolved

        drama_url = f"{self.base_url}/{slug}"
        soup = await self._make_request(drama_url)
        

        try:
            details = {'slug': slug, 'url': drama_url}

            # --- Main Title and Image ---
            title_elem = soup.select_one('h1.film-title')
            details['title'] = title_elem.get_text(strip=True) if title_elem else 'N/A'
            
            img_elem = soup.select_one('div.film-cover img')
            details['image'] = img_elem.get('src') or img_elem.get('data-src') or '' if img_elem else ''

            # --- Synopsis ---
            synopsis_elem = soup.select_one('div.show-synopsis > p')
            details['synopsis'] = synopsis_elem.get_text(" ", strip=True).replace(' Edit Translation', '') if synopsis_elem else ''

            # --- Sidebar Details & Statistics ---
            sidebar_details = soup.select("div.content-side .box")
            for box in sidebar_details:
                header_elem = box.select_one('.box-header h3')
                if not header_elem:
                    continue
                header = header_elem.get_text(strip=True)
                
                if header == 'Details':
                    for item in box.select('li.list-item'):
                        item_text = item.get_text(" ", strip=True)
                        if 'Drama:' in item_text:
                            details['type'] = item_text.replace('Drama:', '').strip()
                        elif 'Country:' in item_text:
                            details['country'] = item_text.replace('Country:', '').strip()
                        elif 'Episodes:' in item_text:
                            details['episodes'] = item_text.replace('Episodes:', '').strip()
                        elif 'Aired:' in item_text:
                            details['aired'] = item_text.replace('Aired:', '').strip()
                        elif 'Aired On:' in item_text:
                            details['aired_on'] = item_text.replace('Aired On:', '').strip()
                        elif 'Original Network:' in item_text:
                            network_elem = item.find('a')
                            details['original_network'] = network_elem.get_text(strip=True) if network_elem else item_text.replace('Original Network:', '').strip()
                        elif 'Duration:' in item_text:
                            details['duration'] = item_text.replace('Duration:', '').strip()
                        elif 'Content Rating:' in item_text:
                            details['content_rating'] = item_text.replace('Content Rating:', '').strip()
                
                elif header == 'Statistics':
                    for item in box.select('li.list-item'):
                        item_text = item.get_text(" ", strip=True)
                        if 'Score:' in item_text:
                            details['score_details'] = item_text.replace('Score:', '').strip()
                        elif 'Ranked:' in item_text:
                            details['ranked'] = item_text.replace('Ranked:', '').strip()
                        elif 'Popularity:' in item_text:
                            details['popularity'] = item_text.replace('Popularity:', '').strip()
                        elif 'Watchers:' in item_text:
                            details['watchers'] = item_text.replace('Watchers:', '').strip()
            
            # --- Main Details (Native Title, Genres, Tags) ---
            for item in soup.select('li.list-item'):
                key_elem = item.find('b')
                if not key_elem: continue
                key = key_elem.get_text(strip=True)
                
                if 'Native Title:' in key:
                    details['native_title'] = item.get_text().replace(key, '', 1).strip()
                elif 'Also Known As:' in key:
                    details['also_known_as'] = [s.strip() for s in item.get_text().replace(key, '', 1).split(',') if s.strip()]
                elif 'Genres:' in key:
                    details['genres'] = [a.get_text(strip=True) for a in item.select('a')]
                elif 'Tags:' in key:
                    tags = [a.get_text(strip=True) for a in item.select('a')]
                    details['tags'] = [t for t in tags if t != '(Vote tags)']

            # --- Overall Rating ---
            rating_elem = soup.select_one('.hfs b')
            details['rating'] = rating_elem.get_text(strip=True) if rating_elem else 'N/A'

            return details

        except Exception as e:
            logger.error(f"Error parsing drama details for '{slug}': {str(e)}")
            raise

    async def get_drama_cast(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get cast information for a drama by slug (or title)"""
        if not re.search(r'\d+-', slug):
            resolved = await self.resolve_slug(slug)
            if resolved:
                slug = resolved

        cast_url = f"{self.base_url}/{slug}/cast"
        soup = await self._make_request(cast_url)
        

        cast_by_role = {}
        try:
            # The page is structured by headers (h3) for roles
            role_headers = soup.find_all('h3', class_='header')
            for header in role_headers:
                role_name = header.get_text(strip=True)
                cast_list = []
                
                cast_container = header.find_next_sibling('ul', class_='list')
                if not cast_container:
                    continue
                
                cast_items = cast_container.find_all('li', class_='list-item')
                for item in cast_items:
                    try:
                        name_elem = item.find('a', class_='text-primary')
                        if not name_elem:
                            continue
                        
                        name = name_elem.find('b').get_text(strip=True) if name_elem.find('b') else name_elem.get_text(strip=True)
                        profile_url = name_elem.get('href', '')

                        character_role = ''
                        role_div = name_elem.find_next_sibling('div')
                        if role_div and role_div.find('small'):
                            character_role = role_div.find('small').get_text(strip=True)
                        
                        img_elem = item.find('img')
                        image = (img_elem.get('src') or img_elem.get('data-src') or '') if img_elem else ''

                        cast_list.append({
                            'name': name,
                            'character': character_role,
                            'image': image,
                            'profile_url': f"{self.base_url}{profile_url}" if profile_url else ''
                        })
                    except Exception as e:
                        logger.error(f"Error parsing individual cast member: {str(e)}")
                        continue
                
                if cast_list:
                    cast_by_role[role_name] = cast_list

            total_cast = sum(len(v) for v in cast_by_role.values())
            return {'cast': cast_by_role, 'total': total_cast}
        except Exception as e:
            logger.error(f"Error parsing cast page: {str(e)}")
            raise

    async def get_drama_episodes(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get episode details for a drama by slug (or title)"""
        if not re.search(r'\d+-', slug):
            resolved = await self.resolve_slug(slug)
            if resolved:
                slug = resolved

        episodes_url = f"{self.base_url}/{slug}/episodes"
        soup = await self._make_request(episodes_url)
        

        try:
            episodes = []
            episode_items = soup.find_all('div', class_='episode')
            
            for item in episode_items:
                try:
                    title_elem = item.select_one('h2.title > a')
                    full_title = title_elem.get_text(strip=True) if title_elem else ''
                    
                    episode_num_match = re.search(r'Episode\s+(\d+)', full_title)
                    episode_num = episode_num_match.group(1) if episode_num_match else ''
                    
                    air_date_elem = item.find('div', class_='air-date')
                    air_date = air_date_elem.get_text(strip=True) if air_date_elem else ''
                    
                    episodes.append({
                        'episode_number': episode_num,
                        'title': full_title,
                        'air_date': air_date
                    })
                except Exception as e:
                    logger.error(f"Error parsing episode: {str(e)}")
                    continue

            return {'episodes': episodes, 'total': len(episodes)}
        except Exception as e:
            logger.error(f"Error parsing episodes: {str(e)}")
            raise

    async def get_episode_details(self, slug: str, episode_number: int) -> Optional[Dict[str, Any]]:
        """Get details for a single episode (description + cover image) from /episode/{n}"""
        if not re.search(r'\d+-', slug):
            resolved = await self.resolve_slug(slug)
            if resolved:
                slug = resolved

        episode_url = f"{self.base_url}/{slug}/episode/{episode_number}"
        soup = await self._make_request(episode_url)


        try:
            data: Dict[str, Any] = {
                'episode_number': str(episode_number),
                'url': episode_url
            }

            # --- Title ---
            title_elem = soup.select_one('h1.film-title, h1')
            data['title'] = title_elem.get_text(strip=True) if title_elem else f'Episode {episode_number}'

            # --- Cover Image ---
            # MDL episode pages use img.img-responsive inside the episode box
            img_elem = (
                soup.select_one('.episode-cover img') or
                soup.select_one('.box-body img.img-responsive') or
                soup.select_one('img.img-responsive') or
                soup.select_one('.film-cover img')
            )
            data['image'] = (img_elem.get('src') or img_elem.get('data-src') or '') if img_elem else ''

            # --- Description ---
            # MDL uses div.show-episode-description for the episode blurb
            desc_elem = (
                soup.select_one('div.show-episode-description') or
                soup.select_one('.episode-description') or
                soup.select_one('div.episode-synopsis') or
                soup.select_one('div.show-synopsis')
            )
            if desc_elem:
                # Strip any nested "Edit Translation" links
                for a in desc_elem.find_all('a'):
                    a.decompose()
                data['description'] = desc_elem.get_text(' ', strip=True)
            else:
                # Fallback: og:description meta tag (always reliable on MDL)
                og_desc = soup.select_one('meta[property="og:description"]')
                data['description'] = og_desc['content'] if og_desc and og_desc.get('content') else ''

            # --- Air Date ---
            # Try direct element first, then scan for "Aired: ..." text in the page
            aired_elem = soup.select_one('div.air-date, span.air-date, .episode-aired')
            if aired_elem:
                data['air_date'] = aired_elem.get_text(strip=True)
            else:
                data['air_date'] = ''
                for el in soup.find_all(['p', 'div', 'span', 'li']):
                    txt = el.get_text(strip=True)
                    if txt.startswith('Aired:') and len(txt) < 60:
                        data['air_date'] = txt.replace('Aired:', '').strip()
                        break

            # --- Rating ---
            rating_elem = soup.select_one('.hfs b, .score')
            data['rating'] = rating_elem.get_text(strip=True) if rating_elem else ''

            # --- Season ---
            data['season'] = ''
            for el in soup.find_all(['p', 'div', 'span', 'li']):
                txt = el.get_text(strip=True)
                if txt.startswith('Season:') and len(txt) < 20:
                    data['season'] = txt.replace('Season:', '').strip()
                    break

            return data
        except Exception as e:
            logger.error(f"Error parsing episode {episode_number} for '{slug}': {str(e)}")
            raise

    async def get_drama_episodes_all(self, slug: str) -> Optional[Dict[str, Any]]:
        """
        Get all episodes with full details (title, air_date, description, image).
        First fetches the episode list page to get episode numbers/titles/dates,
        then concurrently visits each /episode/{n} page for description + image.
        """
        if not re.search(r'\d+-', slug):
            resolved = await self.resolve_slug(slug)
            if resolved:
                slug = resolved

        # Step 1: Get base episode list (title + air_date + episode number)
        episodes_url = f"{self.base_url}/{slug}/episodes"
        soup = await self._make_request(episodes_url)


        try:
            base_episodes = []
            episode_items = soup.find_all('div', class_='episode')

            for item in episode_items:
                try:
                    title_elem = item.select_one('h2.title > a')
                    full_title = title_elem.get_text(strip=True) if title_elem else ''

                    episode_num_match = re.search(r'Episode\s+(\d+)', full_title)
                    episode_num = episode_num_match.group(1) if episode_num_match else ''

                    air_date_elem = item.find('div', class_='air-date')
                    air_date = air_date_elem.get_text(strip=True) if air_date_elem else ''

                    if episode_num:
                        base_episodes.append({
                            'episode_number': episode_num,
                            'title': full_title,
                            'air_date': air_date
                        })
                except Exception as e:
                    logger.error(f"Error parsing base episode item: {str(e)}")
                    continue

            if not base_episodes:
                return {'episodes': [], 'total': 0}

            # Step 2: Concurrently fetch each episode detail page
            async def fetch_detail(ep: Dict[str, Any]) -> Dict[str, Any]:
                try:
                    n = int(ep['episode_number'])
                    detail = await self.get_episode_details(slug, n)
                    if detail:
                        # Merge: prefer base title if detail title is generic
                        ep['description'] = detail.get('description', '')
                        ep['image'] = detail.get('image', '')
                        ep['rating'] = detail.get('rating', '')
                        ep['season'] = detail.get('season', '')
                        # Use air_date from detail page if base is empty
                        if not ep.get('air_date') and detail.get('air_date'):
                            ep['air_date'] = detail['air_date']
                    else:
                        ep['description'] = ''
                        ep['image'] = ''
                        ep['rating'] = ''
                        ep['season'] = ''
                except Exception as e:
                    logger.error(f"Error fetching detail for episode {ep.get('episode_number')}: {str(e)}")
                    ep['description'] = ''
                    ep['image'] = ''
                    ep['rating'] = ''
                    ep['season'] = ''
                return ep

            # Stagger requests slightly to avoid rate limiting (batch of 4 at a time)
            enriched_episodes = []
            batch_size = 4
            for i in range(0, len(base_episodes), batch_size):
                batch = base_episodes[i:i + batch_size]
                results = await asyncio.gather(*[fetch_detail(ep) for ep in batch])
                enriched_episodes.extend(results)
                if i + batch_size < len(base_episodes):
                    await asyncio.sleep(0.5)  # Brief pause between batches

            return {
                'episodes': enriched_episodes,
                'total': len(enriched_episodes)
            }
        except Exception as e:
            logger.error(f"Error in get_drama_episodes_all for '{slug}': {str(e)}")
            return None

    async def get_drama_reviews(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get reviews for a drama by slug (or title)"""
        if not re.search(r'\d+-', slug):
            resolved = await self.resolve_slug(slug)
            if resolved:
                slug = resolved

        reviews_url = f"{self.base_url}/{slug}/reviews"
        soup = await self._make_request(reviews_url)
        

        try:
            reviews = []
            review_items = soup.find_all('div', class_='review')
            
            for item in review_items[:10]:
                try:
                    author_elem = item.find('a', class_='text-primary')
                    author = author_elem.get_text(strip=True) if author_elem else ''
                    
                    rating_elem = item.select_one('.rating-overall .score')
                    rating = rating_elem.get_text(strip=True) if rating_elem else ''
                    
                    content_elem = item.find('div', class_='review-body')
                    content_p = content_elem.find_all('p') if content_elem else []
                    content = "\n".join([p.get_text(" ", strip=True) for p in content_p])
                    
                    date_elem = item.find('small', class_='datetime')
                    date = date_elem.get_text(strip=True) if date_elem else ''
                    
                    reviews.append({
                        'author': author,
                        'rating': rating,
                        'content': content,
                        'date': date
                    })
                except Exception as e:
                    logger.error(f"Error parsing review: {str(e)}")
                    continue

            return {'reviews': reviews, 'total': len(reviews)}
        except Exception as e:
            logger.error(f"Error parsing reviews: {str(e)}")
            raise

    async def get_person_details(self, people_id: str) -> Optional[Dict[str, Any]]:
        """Get person details by ID"""
        person_url = f"{self.base_url}/people/{people_id}"
        soup = await self._make_request(person_url)
        

        try:
            data = {'id': people_id, 'url': person_url}

            name_elem = soup.select_one('h1.film-title')
            data['name'] = name_elem.get_text(strip=True) if name_elem else 'N/A'

            img_elem = soup.select_one('.profile-image img, .box-body img.img-responsive')
            data['image'] = img_elem.get('src') or img_elem.get('data-src') or '' if img_elem else ''
            # The profile <img> is a cropped thumbnail (`_5c`). Expose the full-size
            # variant alongside it so callers that want to archive the photo don't have
            # to know MDL's suffix scheme. Additive: `image` keeps its old value.
            data['image_full'] = self._full_size_image(data['image'])

            # --- Personal Info from sidebar ---
            info = {}
            details_box = soup.select_one('div.box.clear.hidden-sm-down')
            if details_box:
                info_list = details_box.select('ul.list > li.list-item')
                for item in info_list:
                    key_elem = item.find('b')
                    if key_elem:
                        key = key_elem.get_text(strip=True).replace(':', '').strip().lower().replace(' ', '_')
                        value = item.get_text().replace(key_elem.get_text(), '', 1).strip()
                        info[key] = value
            data['personal_info'] = info
            
            # --- Biography ---
            bio_container = soup.select_one('div.col-sm-8.col-lg-12.col-md-12')
            if bio_container:
                # Get all text nodes directly under the div, excluding those in script tags or children tags
                bio_texts = [text.strip() for text in bio_container.find_all(string=True, recursive=False) if text.strip()]
                data['biography'] = " ".join(bio_texts)
            
            # --- Filmography ---
            filmography = {}
            film_headers = soup.select('div.box-body > h5.header')
            for header in film_headers:
                category = header.get_text(strip=True)
                table = header.find_next_sibling('table', class_='film-list')
                if table:
                    entries = []
                    for row in table.select('tbody > tr'):
                        entry = {}
                        entry['year'] = row.select_one('td.year').get_text(strip=True) if row.select_one('td.year') else 'N/A'
                        
                        # The FIRST anchor inside `td.title` wraps the thumbnail <img>, so
                        # `td.title a` matched it and returned empty text — that was the bug
                        # that left every title (and every drama link) blank. The real title
                        # anchor is the bolded one: `<b><a class="text-primary" href="/810684-...">`.
                        # Fall back through the other places MDL keeps the name so a row that
                        # legitimately has no link (upcoming/TBA titles) still reports a title
                        # instead of failing the whole row.
                        title_cell = row.select_one('td.title')
                        title_link = title_cell.select_one('b a[href]') if title_cell else None
                        if title_link is None and title_cell:
                            for anchor in title_cell.select('a[href]'):
                                if anchor.get_text(strip=True):
                                    title_link = anchor
                                    break

                        title = ''
                        href = ''
                        if title_link is not None:
                            title = title_link.get_text(strip=True) or (title_link.get('title') or '').strip()
                            href = title_link.get('href') or ''
                        elif title_cell is not None:
                            # No anchor at all: take the bold heading, then the thumbnail's
                            # alt/title attribute. Never the whole cell's text — that would
                            # swallow the character name and the "add" button.
                            bold = title_cell.find('b')
                            if bold:
                                title = bold.get_text(strip=True)
                            if not title:
                                thumb = title_cell.find('img')
                                if thumb:
                                    title = (thumb.get('alt') or thumb.get('title') or '').strip()

                        entry['title'] = title
                        # `/810684-qing-zai-hao-hao-huo-yi-ci` -> slug + absolute url, which is
                        # what a consumer needs to look the drama up (`/api/id/{slug}`) or to
                        # store as a stable MDL link. Guard on the leading digits so an
                        # unrelated href (a genre or profile link) can never become a slug.
                        slug = href.split('?', 1)[0].strip('/')
                        entry['slug'] = slug if re.match(r'^\d+-', slug) else ''
                        entry['url'] = f"{self.base_url}/{entry['slug']}" if entry['slug'] else ''
                        
                        role_div = row.select_one('td.role > div.name')
                        role_text_div = row.select_one('td.role > div.text-muted')
                        entry['character_name'] = role_div.get_text(strip=True) if role_div else ''
                        entry['role'] = role_text_div.get_text(strip=True) if role_text_div else ''

                        rating_div = row.select_one('td.text-center > div.text-sm')
                        entry['rating'] = rating_div.get_text(strip=True) if rating_div else 'N/A'
                        
                        entries.append(entry)
                    # MDL renders MORE THAN ONE box under the same heading for some
                    # people. Yang Mie Mie (id 133713) has two "Drama" boxes: the main
                    # one with ~46 credits, then a second holding a single credit.
                    # `filmography[category] = entries` let the later box CLOBBER the
                    # earlier one, so the API reported 1 drama for her and silently
                    # dropped the other 46 — the consumer had no way to tell. Merge the
                    # boxes instead, skipping any row whose slug is already recorded for
                    # this category so a genuine MDL repeat cannot double-count.
                    # (Patched 2026-07-14.)
                    bucket = filmography.setdefault(category, [])
                    seen = {e['slug'] for e in bucket if e.get('slug')}
                    for entry in entries:
                        slug = entry.get('slug')
                        if slug and slug in seen:
                            continue
                        if slug:
                            seen.add(slug)
                        bucket.append(entry)
            data['filmography'] = filmography

            return data
        except Exception as e:
            logger.error(f"Error parsing person details for '{people_id}': {str(e)}")
            return None

    async def get_person_photos(self, people_id: str, limit: int = 12) -> Optional[Dict[str, Any]]:
        """Get a person's photo-gallery images (full-size URLs), newest first.

        The gallery at /people/{id}/photos lays each photo out as
        `<a class="block" href="/photos/{hash}_{n}"><img src=".../{hash}_{n}m.jpg"></a>`.
        Anchoring the selector on that href keeps the sidebar's profile picture and the
        site chrome out of the results. `limit` exists because some people have
        hundreds of photos and no caller wants them all in one response.
        """
        photos_url = f"{self.base_url}/people/{people_id}/photos"
        soup = await self._make_request(photos_url)

        try:
            data = {'id': people_id, 'url': photos_url}

            name_elem = soup.select_one('h1.film-title')
            data['name'] = name_elem.get_text(strip=True) if name_elem else 'N/A'

            photos: List[Dict[str, str]] = []
            seen = set()
            for anchor in soup.select('a.block[href^="/photos/"]'):
                img = anchor.find('img')
                if not img:
                    continue
                thumb = img.get('src') or img.get('data-src') or ''
                if not thumb:
                    continue
                full = self._full_size_image(thumb)
                if full in seen:  # MDL occasionally repeats a photo across rows
                    continue
                seen.add(full)
                photos.append({
                    'image': full,
                    'thumbnail': thumb,
                    'page': f"{self.base_url}{anchor.get('href') or ''}",
                })
                if len(photos) >= limit:
                    break

            data['photos'] = photos
            data['total'] = len(photos)
            # An empty gallery is a valid answer, not a 404 — return the envelope so
            # callers can tell "no photos" apart from "no such person".
            return data
        except Exception as e:
            logger.error(f"Error parsing person photos for '{people_id}': {str(e)}")
            return None

    async def get_seasonal_dramas(self, year: int, quarter: int) -> Dict[str, Any]:
        """Get seasonal dramas"""
        seasons = {1: 'winter', 2: 'spring', 3: 'summer', 4: 'fall'}
        season = seasons.get(quarter, 'winter')
        
        seasonal_url = f"{self.base_url}/shows/top?year={year}&season={season}"
        soup = await self._make_request(seasonal_url)
        

        try:
            dramas = []
            drama_items = soup.find_all('div', class_='box')
            
            for item in drama_items[:20]:
                try:
                    title_elem = item.find('h6')
                    if not title_elem or not title_elem.find('a'):
                        continue
                    
                    title = title_elem.get_text(strip=True)
                    link_elem = title_elem.find('a')
                    link = link_elem['href'] if link_elem else ''
                    slug = link.split('/')[-1] if link else ''
                    
                    img_elem = item.find('img', class_='lazy')
                    image = img_elem.get('data-src') if img_elem else ''
                    
                    rating_elem = item.find('span', class_='score')
                    rating = rating_elem.get_text(strip=True) if rating_elem else ''
                    
                    dramas.append({
                        'title': title,
                        'slug': slug,
                        'image': image,
                        'rating': rating,
                        'url': f"{self.base_url}{link}" if link else ''
                    })
                except Exception as e:
                    logger.error(f"Error parsing seasonal drama: {str(e)}")
                    continue

            return {
                "dramas": dramas,
                "total": len(dramas),
                "year": year,
                "quarter": quarter,
                "season": season
            }
        except Exception as e:
            logger.error(f"Error parsing seasonal dramas: {str(e)}")
            return {"dramas": [], "total": 0, "year": year, "quarter": quarter}

    async def get_drama_list(self, list_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific drama list by ID"""
        list_url = f"{self.base_url}/list/{list_id}"
        soup = await self._make_request(list_url)
        

        try:
            if soup.select_one('.alert-danger') and 'private' in soup.select_one('.alert-danger').text.lower():
                raise Exception("This list is private")
            
            title_elem = soup.find('h1')
            title = title_elem.get_text(strip=True) if title_elem else ''
            
            description_elem = soup.select_one('div.box-header .description')
            description = description_elem.get_text(strip=True) if description_elem else ''
            
            dramas = []
            drama_items = soup.select('ul.list-group li.list-group-item')
            
            for item in drama_items:
                try:
                    drama_title_elem = item.select_one('h2.title > a')
                    if not drama_title_elem:
                        continue
                    
                    drama_title = drama_title_elem.get_text(strip=True)
                    link = drama_title_elem['href']
                    slug = link.split('/')[-1] if link else ''
                    
                    img_elem = item.find('img', class_='lazy')
                    image = (img_elem.get('data-src') or img_elem.get('src') or '') if img_elem else ''
                    
                    dramas.append({
                        'title': drama_title,
                        'slug': slug,
                        'image': image,
                        'url': f"{self.base_url}{link}" if link else ''
                    })
                except Exception as e:
                    logger.error(f"Error parsing list item: {str(e)}")
                    continue

            return {
                'title': title,
                'description': description,
                'dramas': dramas,
                'total': len(dramas),
                'url': list_url
            }
        except Exception as e:
            if "private" in str(e).lower():
                raise
            logger.error(f"Error parsing drama list: {str(e)}")
            return None

    async def get_user_drama_list(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user's drama list by user ID"""
        user_list_url = f"{self.base_url}/dramalist/{user_id}"
        soup = await self._make_request(user_list_url)
        

        try:
            if "This user's list is private." in soup.get_text():
                raise Exception("This list is private")
            
            username_elem = soup.select_one('h1.mdl-style-header a')
            username = username_elem.get_text(strip=True) if username_elem else user_id
            
            dramas = []
            list_sections = soup.find_all('div', class_='mdl-style-list')
            
            for section in list_sections:
                status_header = section.find('h3', class_='mdl-style-list-label')
                status = status_header.get_text(strip=True) if status_header else 'Unknown'

                drama_rows = section.select('table > tbody > tr')

                for row in drama_rows:
                    try:
                        title_elem = row.find('a', class_='title')
                        if not title_elem:
                            continue
                            
                        title = title_elem.get_text(strip=True)
                        link = title_elem['href']
                        slug = link.split('/')[-1] if link else ''

                        rating_elem = row.select_one('td.mdl-style-col-score .score')
                        rating = rating_elem.get_text(strip=True) if rating_elem and rating_elem.get_text(strip=True) not in ["0.0", "N/A"] else ''
                        
                        img_elem = row.find('img')
                        image = (img_elem.get('data-src') or img_elem.get('src') or '') if img_elem else ''

                        dramas.append({
                            'title': title,
                            'slug': slug,
                            'status': status,
                            'rating': rating,
                            'image': image,
                            'url': f"{self.base_url}{link}" if link else ''
                        })
                    except Exception as e:
                        logger.error(f"Error parsing user list item: {str(e)}")
                        continue
            
            return {
                'username': username,
                'user_id': user_id,
                'dramas': dramas,
                'total': len(dramas),
                'url': user_list_url
            }
        except Exception as e:
            if "private" in str(e).lower():
                raise
            logger.error(f"Error parsing user drama list: {str(e)}")
            return None

    async def get_drama_recommendations(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get recommendations for a specific drama by slug (or title) with optimized parsing and pagination"""
        if not re.search(r'\d+-', slug):
            resolved = await self.resolve_slug(slug)
            if resolved:
                slug = resolved

        all_recommendations = []
        page = 1
        base_rec_url = f"{self.base_url}/{slug}/recs"
        
        while True:
            rec_url = f"{base_rec_url}?page={page}" if page > 1 else base_rec_url
            soup = await self._make_request(rec_url)
            
            # Updated selector for recommendation items
            rec_items = soup.select("div.box-body.b-t")
            if not rec_items:
                break
                
            page_recs_found = 0
            for item in rec_items:
                try:
                    # --- TITLE + YEAR ---
                    # Updated selector to 'b a'
                    title_elem = item.select_one("b a")
                    if not title_elem:
                        continue
                    
                    title_full = title_elem.get_text(strip=True)
                    # Match "Title (Year)"
                    title_match = re.match(r"(.+?)\s*\((\d{4})\)", title_full)
                    title = title_match.group(1).strip() if title_match else title_full
                    year = title_match.group(2) if title_match else ""

                    link = title_elem["href"] if title_elem else ""
                    slug_rec = link.split("/")[-1] if link else ""

                    # --- IMAGE ---
                    img_elem = item.select_one("img")
                    image = img_elem.get("data-src") or img_elem.get("src") if img_elem else ""

                    # --- RATING ---
                    rating_elem = item.select_one(".score")
                    rating = rating_elem.get_text(strip=True) if rating_elem else ""

                    # --- RECOMMENDED BY ---
                    # Updated selector to 'span.recs-author a'
                    author_elem = item.select_one("span.recs-author a")
                    recommended_by = author_elem.get_text(strip=True) if author_elem else ""

                    # --- VOTES ---
                    # Updated selector to '.like-cnt'
                    votes_elem = item.select_one(".like-cnt")
                    votes = votes_elem.get_text(strip=True) if votes_elem else "0"

                    # --- REASON ---
                    # Updated selector to 'div.recs-body'
                    reason_container = item.select_one("div.recs-body")
                    reason_lines = []
                    if reason_container:
                        # Clean up the container by removing metadata elements before extracting text
                        for meta in reason_container.select(".recs-author, .like-cnt, .btn-menu, .more-recs"):
                            meta.decompose()
                            
                        raw_text = reason_container.get_text("\n", strip=True)
                        # Remove remaining "Recommended by" text node if it exists
                        if "Recommended by" in raw_text:
                            raw_text = raw_text.replace("Recommended by", "").strip()
                            
                        lines = [p.strip() for p in raw_text.split("\n") if p.strip()]
                        
                        # Robustly filter out metadata (votes, "Recommended by", and author name)
                        # We truncate the list when we encounter the "Recommended by" marker or the author name.
                        cleaned_lines = []
                        for line in lines:
                            if line == "Recommended by" or (recommended_by and line == recommended_by):
                                break
                            if line == votes and len(line) < 10: # Likely the vote count standalone
                                continue
                            cleaned_lines.append(line)
                        lines = cleaned_lines
                        
                        # Handle requested reason parsing: split by newline and strip.
                        # If the list seems to start with dashes, it's likely the old style bulleted list.
                        # But user specified: "if not bulleted" - we'll handle both.
                        is_bulleted = any(line.startswith("-") for line in lines)
                        if is_bulleted:
                            reason_lines = [line.lstrip("-").strip() for line in lines if line.startswith("-")]
                            if not reason_lines: # Fallback if dash detection failed but some text exists
                                reason_lines = lines
                        else:
                            reason_lines = lines

                    all_recommendations.append({
                        "title": title,
                        "year": year,
                        "slug": slug_rec,
                        "url": f"{self.base_url}{link}" if link else "",
                        "image": image,
                        "rating": rating,
                        "reasons": reason_lines,
                        "recommended_by": recommended_by,
                        "votes": votes
                    })
                    page_recs_found += 1
                except Exception as e:
                    logger.error(f"Error parsing recommendation: {str(e)}")
                    continue
            
            if page_recs_found == 0:
                break
                
            # Check for next page in pagination
            # Usually: <li class="page-item next"><a class="page-link" href="...">Next</a></li>
            # Or sometimes just a link with 'next' in rel
            next_link = soup.select_one("li.page-item.next:not(.disabled) a.page-link") or soup.select_one("a.page-link[rel='next']")
            if not next_link or page >= 5: # Limit pagination to 5 pages to avoid rate limits/timeouts
                break
                
            page += 1
            await asyncio.sleep(0.5) # Anti-ban delay

        return {
            "recommendations": all_recommendations,
            "total": len(all_recommendations),
            "url": base_rec_url,
            "pages_fetched": page
        }

    async def get_airing_calendar(self) -> Optional[Dict[str, Any]]:
        """Get currently airing dramas grouped by day of the week"""
        calendar_url = f"{self.base_url}/calendar"
        soup = await self._make_request(calendar_url)
        

        try:
            days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            calendar_data = {day: [] for day in days}
            total_dramas = 0

            # The page uses <h2> tags for each day, followed by a <div class="row"> containing the dramas
            for day in days:
                day_header = soup.find('h2', string=lambda t: t and day in t)
                if not day_header:
                    continue
                
                # Note: The static HTML might contain all days, or only today if loaded via JS.
                # Here we parse whatever is available in the static HTML.
                dramas_container = day_header.find_next_sibling('div', class_='row')
                if not dramas_container:
                    continue
                    
                drama_items = dramas_container.select('.col-md-6, .col-lg-6')
                for item in drama_items:
                    try:
                        # Extract title and URL
                        title_elem = item.select_one('a.text-primary._600')
                        title = title_elem.get_text(strip=True) if title_elem else ''
                        
                        # Use the cover image link for the base drama URL and slug
                        cover_link = item.select_one('.cover-sm a')
                        link = cover_link['href'] if cover_link else ''
                        slug = link.split('/')[-1] if link else ''
                        url = f"{self.base_url}{link}" if link else ''
                        
                        # Extract image
                        img_elem = item.select_one('img.img-responsive')
                        image = (img_elem.get('src') or img_elem.get('data-src') or '') if img_elem else ''
                        
                        # Extract episode number
                        ep_elem = item.select_one('.text-sm')
                        episode = ep_elem.get_text(strip=True) if ep_elem else ''
                        
                        # Extract air time. Strip the extra info icon text if present
                        time_elem = item.select_one('.release-time')
                        air_time = ''
                        if time_elem:
                            air_time = time_elem.get_text(" ", strip=True)
                            
                        # Extract network if available (it might be in .calendar-popover-title or similar)
                        network_elem = item.select_one('.calendar-popover-title')
                        network = network_elem.get_text(strip=True) if network_elem else ''
                        
                        # Try to extract rating (though it may not be present in calendar view)
                        rating_elem = item.select_one('.score')
                        rating = rating_elem.get_text(strip=True) if rating_elem else ''
                        
                        if title:
                            calendar_data[day].append({
                                'title': title,
                                'slug': slug,
                                'url': url,
                                'image': image,
                                'rating': rating,
                                'episode': episode,
                                'air_time': air_time,
                                'network': network
                            })
                            total_dramas += 1
                    except Exception as e:
                        logger.error(f"Error parsing calendar drama item: {str(e)}")
                        continue

            return {
                "days": calendar_data,
                "total": total_dramas,
                "url": calendar_url
            }
        except Exception as e:
            logger.error(f"Error parsing airing calendar: {str(e)}")
            return None
