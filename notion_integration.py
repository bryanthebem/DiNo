# notion_integration.py (Atualizado)

from notion_client import Client
import os
from dotenv import load_dotenv
import json
import re
from datetime import datetime

# Carregar as variáveis de ambiente
load_dotenv()

class NotionIntegration:
    def __init__(self):
        self.token = os.getenv("NOTION_TOKEN")
        self.notion = Client(auth=self.token)
        

    def extract_database_id(self, url):
        match = re.search(r"([a-f0-9]{32})", url)
        if match:
            return match.group(1)
        return None

    def search_in_database(self, url, search_term, filter_property, property_type="rich_text"):
        database_id = self.extract_database_id(url)
        if not database_id:
            return "ID da base de dados não encontrado na URL."

        filter_criteria = {"property": filter_property}

        if property_type == "rich_text":
            filter_criteria["rich_text"] = {"contains": search_term}
        elif property_type == "title":
            filter_criteria["title"] = {"contains": search_term}
        elif property_type == "status":
            filter_criteria["status"] = {"equals": search_term}
        elif property_type == "select":
             filter_criteria["select"] = {"equals": search_term}
        elif property_type == "person":
            pessoa_id = self.search_id_person(search_term)
            if pessoa_id:
                filter_criteria["people"] = {"contains": pessoa_id}
            else:
                return {"results": []}

        try:
            result = self.notion.databases.query(database_id, filter=filter_criteria)
            return result
        except Exception as e:
            return f"Erro ao buscar no Notion: {e}"

    def get_database_properties(self, url):
        database_id = self.extract_database_id(url)
        if not database_id:
            return "ID da base de dados não encontrado na URL."
        try:
            result = self.notion.databases.retrieve(database_id)
            return result['properties']
        except Exception as e:
            return f"Erro ao obter propriedades: {e}"
    
    def search_id_person(self, search_term):
        try:
            users = self.notion.users.list()
            search_term_lower = search_term.lower()
            for user in users.get("results", []):
                if search_term_lower in user.get("name", "").lower() or search_term_lower == user.get("person", {}).get("email", ""):
                    return user.get("id")
            return None
        except Exception as e:
            print(f"Erro ao buscar usuários: {e}")
            return None

    def get_database_count(self, url):
        database_id = self.extract_database_id(url)
        if not database_id:
            return "ID da base de dados não encontrado na URL."
        try:
            result = self.notion.databases.query(database_id)
            return len(result['results'])
        except Exception as e:
            return f"Erro ao contar páginas: {e}"

    def insert_into_database(self, url, properties):
        database_id = self.extract_database_id(url)
        if not database_id:
            return "ID da base de dados não encontrado na URL."
        try:
            response = self.notion.pages.create(parent={"database_id": database_id}, properties=properties)
            return response
        except Exception as e:
            return str(e)
            
    def build_page_properties(self, title, properties_dict):
        db_url = os.getenv("NOTION_URL_CONFIGURADA")
        schema = self.get_database_properties(db_url)
        
        title_prop_name = next((name for name, data in schema.items() if data['type'] == 'title'), 'Nome')

        page_properties = {
            title_prop_name: {"title": [{"text": {"content": title}}]}
        }

        for prop_name, prop_value in properties_dict.items():
            prop_type = schema.get(prop_name, {}).get('type')
            
            if prop_type == 'status':
                page_properties[prop_name] = {"status": {"name": prop_value}}
            elif prop_type == 'multi_select':
                tags = [{"name": tag.strip()} for tag in prop_value.split(',')]
                page_properties[prop_name] = {"multi_select": tags}
            elif prop_type == 'people':
                user_id = self.search_id_person(prop_value)
                if user_id:
                    page_properties[prop_name] = {"people": [{"id": user_id}]}
                else:
                    print(f"Aviso: Usuário '{prop_value}' não encontrado. Propriedade '{prop_name}' será ignorada.")
            elif prop_type == 'rich_text': 
                page_properties[prop_name] = {"rich_text": [{"text": {"content": prop_value}}]}
            
        return page_properties

    # --- FUNÇÃO ATUALIZADA ---
    def extract_value_from_property(self, prop_data, prop_type):
        try:
            if prop_type == 'title':
                return prop_data.get('title', [{}])[0].get('plain_text', '')
            
            elif prop_type == 'rich_text':
                return "".join([part.get('plain_text', '') for part in prop_data.get('rich_text', [])])
            
            # --- AJUSTE AQUI ---
            elif prop_type == 'status':
                status_info = prop_data.get('status')
                return status_info.get('name', '') if status_info else ''
            
            # --- E AJUSTE AQUI ---
            elif prop_type == 'select':
                select_info = prop_data.get('select')
                return select_info.get('name', '') if select_info else ''
            
            elif prop_type == 'multi_select':
                return ", ".join([tag.get('name', '') for tag in prop_data.get('multi_select', [])])
            
            elif prop_type == 'people':
                return ", ".join([person.get('name', '') for person in prop_data.get('people', [])])
            
            elif prop_type == 'date':
                date_info = prop_data.get('date')
                if date_info and date_info.get('start'):
                    start_date = datetime.fromisoformat(date_info['start']).strftime('%d/%m/%Y')
                    return start_date
                return ''
            
            elif prop_type == 'url':
                return prop_data.get('url', '')
            
            elif prop_type == 'number':
                return prop_data.get('number', '')
                
        except (IndexError, TypeError):
            return ''
        return ''

    def get_properties_for_interaction(self, url):
        all_props = self.get_database_properties(url)
        if isinstance(all_props, str):
            return None

        properties_to_ask = []
        title_prop = None
        excluded_types = ['rollup', 'created_by', 'created_time', 'last_edited_by', 'last_edited_time', 'formula']
        
        for prop_name, prop_data in all_props.items():
            prop_type = prop_data.get('type')
            
            if prop_type in excluded_types:
                continue

            prop_info = {'name': prop_name, 'type': prop_type, 'options': None}

            if prop_type == 'select':
                prop_info['options'] = [opt['name'] for opt in prop_data.get('select', {}).get('options', [])]
            elif prop_type == 'multi_select':
                prop_info['options'] = [opt['name'] for opt in prop_data.get('multi_select', {}).get('options', [])]
            elif prop_type == 'status':
                prop_info['options'] = [opt['name'] for opt in prop_data.get('status', {}).get('options', [])]

            if prop_type == 'title':
                title_prop = prop_info
            else:
                properties_to_ask.append(prop_info)

        if title_prop:
            properties_to_ask.insert(0, title_prop)
            
        return properties_to_ask

    def format_page_for_embed(self, page_result, fields_inline=True, display_properties=None):
        properties = page_result.get('properties', {})
        page_url = page_result.get('url', '#')
        
        title = "Card sem título"
        fields = []

        props_to_iterate = display_properties if display_properties is not None else properties.keys()

        for prop_name in props_to_iterate:
            prop_data = properties.get(prop_name)
            if not prop_data:
                continue

            prop_type = prop_data.get('type')
            value = self.extract_value_from_property(prop_data, prop_type)

            if prop_type == 'title':
                title = value if value else title
                continue
            
            if value:
                fields.append({'name': prop_name, 'value': str(value), 'inline': fields_inline})

        return {'title': title, 'url': page_url, 'fields': fields}