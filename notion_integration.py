# notion_integration.py (Revisado com a função get_page)

from notion_client import Client
import os
from dotenv import load_dotenv
import json
import re
from datetime import datetime

load_dotenv()

class NotionAPIError(Exception):
    """Exceção customizada para erros da API do Notion."""
    pass

class NotionIntegration:
    def __init__(self):
        self.token = os.getenv("NOTION_TOKEN")
        if not self.token:
            raise ValueError("O token do Notion (NOTION_TOKEN) não foi encontrado no seu ambiente.")
        self.notion = Client(auth=self.token)

    def _format_property_value(self, prop_type: str, prop_value):
        """Função auxiliar para formatar um valor para a API do Notion."""
        if prop_type == 'title':
            return {"title": [{"text": {"content": prop_value}}]}
        elif prop_type == 'rich_text':
            return {"rich_text": [{"text": {"content": prop_value}}]}
        elif prop_type == 'status':
            return {"status": {"name": prop_value}}
        elif prop_type == 'select':
            value = prop_value[0] if isinstance(prop_value, list) else prop_value
            return {"select": {"name": value}}
        elif prop_type == 'multi_select':
            tags_to_add = []
            if isinstance(prop_value, list):
                tags_to_add = prop_value
            elif isinstance(prop_value, str):
                tags_to_add = [tag.strip() for tag in prop_value.split(',') if tag.strip()]
            return {"multi_select": [{"name": tag} for tag in tags_to_add]}
        elif prop_type == 'date':
            if not prop_value: return None
            date_formats = ["%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"]
            date_obj = None
            for fmt in date_formats:
                try:
                    date_obj = datetime.strptime(prop_value, fmt)
                    break
                except ValueError: continue
            if date_obj:
                return {"date": {"start": date_obj.strftime('%Y-%m-%d')}}
            else:
                print(f"Aviso: Não foi possível interpretar a data '{prop_value}'.")
                return None
        elif prop_type == 'people':
            try:
                user_id = self.search_id_person(prop_value)
                if user_id: return {"people": [{"id": user_id}]}
            except NotionAPIError as e: print(f"Aviso: {e}. Propriedade 'people' será ignorada.")
        return None

    def extract_database_id(self, url):
        match = re.search(r"([a-f0-9]{32})", url)
        if match: return match.group(1)
        return None

    def search_in_database(self, url, search_term, filter_property, property_type="rich_text"):
        database_id = self.extract_database_id(url)
        if not database_id: raise NotionAPIError("ID da base de dados não encontrado na URL.")
        filter_criteria = {"property": filter_property}
        if property_type == "rich_text": filter_criteria["rich_text"] = {"contains": search_term}
        elif property_type == "title": filter_criteria["title"] = {"contains": search_term}
        elif property_type == "status": filter_criteria["status"] = {"equals": search_term}
        elif property_type == "select": filter_criteria["select"] = {"equals": search_term}
        elif property_type == "person":
            pessoa_id = self.search_id_person(search_term)
            if pessoa_id: filter_criteria["people"] = {"contains": pessoa_id}
            else: return {"results": []}
        try:
            return self.notion.databases.query(database_id, filter=filter_criteria)
        except Exception as e: raise NotionAPIError(f"Erro ao buscar no Notion: {e}")

    def get_database_properties(self, url):
        database_id = self.extract_database_id(url)
        if not database_id: raise NotionAPIError("ID da base de dados não encontrado na URL.")
        try:
            return self.notion.databases.retrieve(database_id)['properties']
        except Exception as e: raise NotionAPIError(f"Erro ao obter propriedades do Notion: {e}")

    def search_id_person(self, search_term):
        try:
            users = self.notion.users.list()
            search_term_lower = search_term.lower()
            for user in users.get("results", []):
                if search_term_lower in user.get("name", "").lower() or search_term_lower == user.get("person", {}).get("email", ""):
                    return user.get("id")
            return None
        except Exception as e:
            print(f"Erro ao buscar usuários do Notion: {e}")
            raise NotionAPIError(f"Não foi possível buscar os usuários no Notion.")

    def get_database_count(self, url):
        database_id = self.extract_database_id(url)
        if not database_id: raise NotionAPIError("ID da base de dados não encontrado na URL.")
        try:
            return len(self.notion.databases.query(database_id)['results'])
        except Exception as e: raise NotionAPIError(f"Erro ao contar páginas no Notion: {e}")

    def insert_into_database(self, url, properties):
        database_id = self.extract_database_id(url)
        if not database_id: raise NotionAPIError("ID da base de dados não encontrado na URL.")
        try:
            return self.notion.pages.create(parent={"database_id": database_id}, properties=properties)
        except Exception as e: raise NotionAPIError(f"Erro ao criar a página no Notion: {e}")

    def build_page_properties(self, title, properties_dict):
        db_url = os.getenv("NOTION_URL_CONFIGURADA")
        schema = self.get_database_properties(db_url)
        page_properties = {}
        title_prop_name = next((name for name, data in schema.items() if data['type'] == 'title'), None)
        if title_prop_name:
            page_properties[title_prop_name] = self._format_property_value('title', title)
        for prop_name, prop_value in properties_dict.items():
            prop_data = schema.get(prop_name)
            if not prop_data:
                print(f"Aviso: Propriedade '{prop_name}' não encontrada. Será ignorada.")
                continue
            formatted_prop = self._format_property_value(prop_data.get('type'), prop_value)
            if formatted_prop:
                page_properties[prop_name] = formatted_prop
        return page_properties

    def build_update_payload(self, prop_name: str, prop_type: str, prop_value):
        formatted_prop = self._format_property_value(prop_type, prop_value)
        if formatted_prop:
            return {prop_name: formatted_prop}
        return {}
    
    def extract_value_from_property(self, prop_data, prop_type):
        # ... (código sem alterações)
        try:
            if prop_type == 'title': return prop_data.get('title', [{}])[0].get('plain_text', '')
            elif prop_type == 'rich_text': return "".join([part.get('plain_text', '') for part in prop_data.get('rich_text', [])])
            elif prop_type == 'status': return prop_data.get('status', {}).get('name', '')
            elif prop_type == 'select': return prop_data.get('select', {}).get('name', '')
            elif prop_type == 'multi_select': return ", ".join([tag.get('name', '') for tag in prop_data.get('multi_select', [])])
            elif prop_type == 'people': return ", ".join([person.get('name', '') for person in prop_data.get('people', [])])
            elif prop_type == 'date':
                date_info = prop_data.get('date')
                if date_info and date_info.get('start'): return datetime.fromisoformat(date_info['start']).strftime('%d/%m/%Y')
                return ''
            elif prop_type == 'url': return prop_data.get('url', '')
            elif prop_type == 'number': return prop_data.get('number', '')
        except (IndexError, TypeError): return ''
        return ''


    def get_properties_for_interaction(self, url):
        # ... (código sem alterações)
        all_props = self.get_database_properties(url)
        properties_to_ask, title_prop = [], None
        excluded_types = ['rollup', 'created_by', 'created_time', 'last_edited_by', 'last_edited_time', 'formula']
        for prop_name, prop_data in all_props.items():
            prop_type = prop_data.get('type')
            if prop_type in excluded_types: continue
            prop_info = {'name': prop_name, 'type': prop_type, 'options': None}
            if prop_type == 'select': prop_info['options'] = [opt['name'] for opt in prop_data.get('select', {}).get('options', [])]
            elif prop_type == 'multi_select': prop_info['options'] = [opt['name'] for opt in prop_data.get('multi_select', {}).get('options', [])]
            elif prop_type == 'status': prop_info['options'] = [opt['name'] for opt in prop_data.get('status', {}).get('options', [])]
            if prop_type == 'title': title_prop = prop_info
            else: properties_to_ask.append(prop_info)
        if title_prop: properties_to_ask.insert(0, title_prop)
        return properties_to_ask

    def format_page_for_embed(self, page_result, fields_inline=True, display_properties=None):
        # ... (código sem alterações)
        properties = page_result.get('properties', {})
        page_url, title = page_result.get('url', '#'), "Card sem título"
        fields = []
        props_to_iterate = display_properties if display_properties is not None else properties.keys()
        for prop_name in props_to_iterate:
            prop_data = properties.get(prop_name)
            if not prop_data: continue
            prop_type = prop_data.get('type')
            value = self.extract_value_from_property(prop_data, prop_type)
            if prop_type == 'title': title = value if value else title; continue
            if value: fields.append({'name': prop_name, 'value': str(value), 'inline': fields_inline})
        return {'title': title, 'url': page_url, 'fields': fields}

    def update_page(self, page_id: str, properties: dict):
        try:
            return self.notion.pages.update(page_id=page_id, properties=properties)
        except Exception as e: raise NotionAPIError(f"Erro ao atualizar a página no Notion: {e}")

    # --- NOVA FUNÇÃO ---
    def get_page(self, page_id: str):
        """Busca os dados de uma única página pelo seu ID."""
        try:
            return self.notion.pages.retrieve(page_id=page_id)
        except Exception as e:
            raise NotionAPIError(f"Erro ao buscar a página no Notion: {e}")