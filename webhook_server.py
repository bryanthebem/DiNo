import logging
import threading
import asyncio
from flask import Flask, request
import discord
from discord.ext import commands  # <-- ADICIONADO AQUI

# Importações dos seus módulos existentes
from notion_integration import NotionIntegration
from config_utils import load_config
import json
import re

# Função para extrair o ID do Tópico de uma URL do Discord
def extract_thread_id_from_url(url: str) -> int | None:
    if not url:
        return None
    match = re.search(r'/(\d+)$', url)
    return int(match.group(1)) if match else None

class WebhookServer:
    def __init__(self, bot: commands.Bot, notion_integration: NotionIntegration): # <-- CORRIGIDO AQUI
        self.app = Flask(__name__)
        self.bot = bot
        self.notion = notion_integration
        # Adiciona a rota que o Notion irá chamar.
        # Note que o endpoint é assíncrono para interagir com o bot.
        self.app.route("/notion-webhook", methods=["POST"])(self.handle_notion_webhook)

    def run(self):
        """Roda o servidor Flask em uma thread separada para não bloquear o bot."""
        threading.Thread(target=lambda: self.app.run(host='0.0.0.0', port=8080), daemon=True).start()
        logging.info("Servidor de Webhook iniciado na porta 8080.")

    def find_config_for_database(self, database_id: str) -> dict | None:
        """Encontra a configuração de canal correspondente a um ID de banco de dados do Notion."""
        try:
            with open('configs.json', 'r', encoding='utf-8') as f:
                all_configs = json.load(f)
            
            for server_id, server_config in all_configs.items():
                for channel_id, channel_config in server_config.get("channels", {}).items():
                    if 'notion_url' not in channel_config:
                        continue
                    db_id_from_url = self.notion.extract_database_id(channel_config['notion_url'])
                    if db_id_from_url == database_id:
                        # Retorna a config e adiciona os IDs para uso posterior
                        channel_config['guild_id'] = server_id
                        channel_config['channel_id'] = channel_id
                        return channel_config
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error(f"Erro ao ler configs.json: {e}")
            return None
        return None

    async def process_notification(self, data: dict):
        """Função assíncrona que processa a lógica da notificação."""
        try:
            # 1. Extrair IDs do payload do Notion
            page_id = data.get('page', {}).get('id')
            database_id = data.get('database', {}).get('id')

            if not page_id or not database_id:
                logging.warning("Webhook recebido sem ID de página ou de banco de dados.")
                return

            # 2. Encontrar a configuração do bot para este banco de dados
            config = self.find_config_for_database(database_id)
            if not config:
                logging.info(f"Nenhuma configuração encontrada para o database {database_id}.")
                return

            # 3. Lógica da Notificação em Tópico
            if config.get("topic_notifications_enabled"):
                logging.info(f"Processando notificação de tópico para a página {page_id}")
                
                # Pega o nome da propriedade que armazena o link do tópico
                topic_prop_name = config.get('topic_link_property_name')
                if not topic_prop_name:
                    logging.warning(f"Notificação de tópico habilitada, mas nenhuma propriedade de link definida para o canal {config['channel_id']}.")
                    return

                # Busca os dados atualizados da página no Notion
                page_details = self.notion.get_page(page_id)
                page_properties = page_details.get('properties', {})
                
                # Extrai a URL do tópico da propriedade correta
                topic_link_prop = page_properties.get(topic_prop_name)
                if not topic_link_prop:
                    return 

                topic_url = self.notion.extract_value_from_property(topic_link_prop, topic_link_prop['type'])
                thread_id = extract_thread_id_from_url(topic_url)

                if thread_id:
                    # Tenta obter o canal (tópico) no Discord
                    thread = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                    
                    if thread:
                        display_props = config.get('display_properties', [])
                        embed = self.notion.format_page_for_embed(page_details, display_props)
                        
                        await thread.send("🔔 **Card Atualizado no Notion!**", embed=embed)
                        logging.info(f"Notificação enviada para o tópico {thread_id}.")
                    else:
                        logging.warning(f"Não foi possível encontrar o tópico com ID {thread_id}.")
            
            # TODO: Implementar lógica para "Canal Fixo" e "DM" aqui, se desejar.

        except Exception as e:
            logging.error(f"Erro ao processar webhook do Notion: {e}", exc_info=True)

    async def handle_notion_webhook(self):
        """Endpoint que recebe a chamada do Notion."""
        if request.method == 'POST':
            # Delega o processamento para uma função async que pode interagir com o bot
            # Usa run_coroutine_threadsafe para chamar código async de uma thread sync (Flask)
            asyncio.run_coroutine_threadsafe(self.process_notification(request.json), self.bot.loop)
            return "OK", 200
        return "Método não permitido", 405