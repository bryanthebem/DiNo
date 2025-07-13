# webhook_server.py (Versão Final com Lógica de Regras Dinâmicas)

import logging
import threading
import asyncio
from flask import Flask, request, jsonify
import discord
from discord.ext import commands

from notion_integration import NotionIntegration
from config_utils import load_config
import json
import re

def extract_thread_id_from_url(url: str) -> int | None:
    if not url: return None
    match = re.search(r'/(\d+)$', url)
    return int(match.group(1)) if match else None

class WebhookServer:
    def __init__(self, bot: commands.Bot, notion_integration: NotionIntegration):
        self.app = Flask(__name__)
        self.bot = bot
        self.notion = notion_integration
        # Adiciona uma rota para o webhook do Notion, aceitando apenas POST
        self.app.route("/notion-webhook", methods=["POST"])(self.handle_notion_webhook)

    def run(self):
        # Inicia o servidor Flask em uma thread separada para não bloquear o bot
        threading.Thread(target=lambda: self.app.run(host='0.0.0.0', port=8080), daemon=True).start()
        logging.info("Servidor de Webhook iniciado na porta 8080.")

    def find_config_for_database(self, database_id: str) -> dict | None:
        """Encontra a configuração do bot (servidor/canal) para um ID de banco de dados Notion."""
        try:
            with open('configs.json', 'r', encoding='utf-8') as f:
                all_configs = json.load(f)
            # Itera por todas as configurações de todos os servidores
            for server_id, server_config in all_configs.items():
                for channel_id, channel_config in server_config.get("channels", {}).items():
                    if 'notion_url' not in channel_config: continue
                    # Extrai o ID do banco de dados da URL salva e compara
                    db_id_from_url = self.notion.extract_database_id(channel_config['notion_url'])
                    if db_id_from_url == database_id:
                        # Se encontrar, anexa os IDs do Discord à config e retorna
                        channel_config['guild_id'] = server_id
                        channel_config['channel_id'] = channel_id
                        return channel_config
        except (FileNotFoundError, json.JSONDecodeError): return None
        return None

    def format_message(self, template: str, page_details: dict) -> str:
        """Substitui placeholders no template da mensagem com dados reais do card."""
        properties = page_details.get('properties', {})

        # Encontra o nome da propriedade de título dinamicamente
        title_prop_name = next((name for name, data in properties.items() if data.get('type') == 'title'), 'Nome')
        card_title = self.notion.extract_value_from_property(properties.get(title_prop_name, {}), 'title')

        message = template.replace('{card_title}', card_title)

        # Adiciona a substituição de outras variáveis se necessário no futuro
        # Ex: {user_name}, {card_url}, etc.

        return message

    async def process_page_update(self, data: dict):
        """Processa as regras de notificação para um evento de atualização de página."""
        try:
            page_id = data.get('id')
            database_id = data.get('parent', {}).get('database_id')
            if not page_id or not database_id: return

            # Encontra a configuração do canal associada a este banco de dados
            config = self.find_config_for_database(database_id)
            if not config: return

            rules = config.get('notification_rules', [])
            if not rules: return # Se não há regras, não faz nada

            logging.info(f"Verificando {len(rules)} regra(s) para a página {page_id}")

            page_properties = data.get('properties', {})

            # Itera sobre cada regra que o usuário criou
            for rule in rules:
                trigger_prop_name = rule.get('trigger_property_name')
                trigger_value_name = rule.get('trigger_value_name')

                if not trigger_prop_name or not trigger_value_name:
                    continue # Pula regras mal configuradas

                # Pega os dados da propriedade que a regra está monitorando
                prop_data = page_properties.get(trigger_prop_name)
                if not prop_data:
                    continue

                # Extrai o valor atual da propriedade que foi alterada
                current_value = self.notion.extract_value_from_property(prop_data, prop_data['type'])

                # A CONDIÇÃO PRINCIPAL: O valor atual da propriedade é igual ao valor do gatilho da regra?
                if current_value.lower() == trigger_value_name.lower():
                    logging.info(f"Regra satisfeita! Gatilho: '{trigger_prop_name}' é '{trigger_value_name}'.")

                    # Se a condição foi satisfeita, formata a mensagem e envia
                    message = self.format_message(rule.get('message_template', ''), data)
                    message = message.replace('{trigger_value}', current_value) # Substitui o valor do gatilho

                    # Descobre para qual tópico enviar a mensagem
                    topic_prop_name = config.get('topic_link_property_name')
                    if not topic_prop_name: continue

                    topic_link_prop = page_properties.get(topic_prop_name)
                    if not topic_link_prop: continue

                    topic_url = self.notion.extract_value_from_property(topic_link_prop, topic_link_prop['type'])
                    thread_id = extract_thread_id_from_url(topic_url)

                    if thread_id:
                        # Busca o tópico no Discord e envia a mensagem
                        thread = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                        if thread:
                            await thread.send(message)
                            logging.info(f"Notificação da regra '{rule['rule_id'][:8]}' enviada para o tópico {thread_id}.")

                    # Para a verificação após encontrar a primeira regra que corresponde
                    # para evitar enviar múltiplas notificações para a mesma atualização.
                    break

        except Exception as e:
            logging.error(f"Erro CRÍTICO ao processar atualização de página: {e}", exc_info=True)

    def handle_notion_webhook(self):
        if request.method == 'POST':
            data = request.json

            # O Notion envia um desafio na primeira vez que você configura o webhook.
            # Este código responde ao desafio para confirmar a URL.
            if data and data.get("type") == "url_verification":
                challenge = data.get("challenge")
                logging.info(f"Recebido desafio de verificação do Notion: {challenge}")
                return jsonify({"challenge": challenge})

            # --- LÓGICA ATUALIZADA ---
            # O payload de atualização de propriedade vem direto no corpo da requisição
            # Assumimos que é uma atualização de página/propriedade
            event_type = data.get('event', 'page.updated')
            logging.info(f"Webhook do tipo '{event_type}' recebido.")
            if event_type == "page.updated":
                 # Roda a lógica de processamento de forma segura para não travar o bot
                asyncio.run_coroutine_threadsafe(self.process_page_update(data.get('page', data)), self.bot.loop)

            return "OK", 200

        return "Método não permitido", 405