# coding=utf-8
from __future__ import absolute_import
from uuid import getnode as get_mac
from octoprint.server import user_permission
from octoprint.util import RepeatedTimer
from octoprint.util import version
from octoprint.events import Events
from octoprint.filemanager.analysis import QueueEntry
from datetime import datetime
from past.builtins import basestring
import requests
import flask
import json
import time
import octoprint.plugin
import base64
import trimesh
import os
import _thread
import subprocess


class AiPrintBoxPlugin(octoprint.plugin.SettingsPlugin,
						  octoprint.plugin.EventHandlerPlugin,
						  octoprint.plugin.StartupPlugin,
						  octoprint.plugin.ShutdownPlugin,
						  octoprint.plugin.AssetPlugin,
						  octoprint.plugin.TemplatePlugin,
						  octoprint.plugin.SimpleApiPlugin,
						  octoprint.printer.PrinterCallback):

	def __init__(self):
		self._mqtt = None
		self._mqtt_connected = False
		self._mqtt_tls_set = False
		self._current_task_id = None
		self.server_host = "http://139.159.151.243:8080/fileoperation/"
		self.mmf_status_updater = None
		self._current_action_code = "999"
		self._current_temp_hotend = 0
		self._current_temp_bed = 0
		self._mmf_print = False
		self._printer_status = {"000": "free",
								"100": "prepare",
								"101": "printing",
								"102": "paused",
								"103": "resumed",
								"104": "printing",
								"201": "downloading",
								"301": "SlicingStarted",
								"302": "SlicingDone",
								"303": "SlicingFailed",
								"304": "SlicingCancelled",
								"305": "SlicingProfileAdded",
								"306": "SlicingProfileModified",
								"307": "SlicingProfileDeleted",
								"999": "offline"}

	def initialize(self):
		self._printer.register_callback(self)

	# ~~ SettingsPlugin mixin
	def get_settings_defaults(self):
		return dict(
			supported_printers = [],			
			printer_manufacturer = "CARS",
			printer_model = "CARS-C8",
			printer_serial_number = "",
			printer_firmware_version = "",
			registration_complete = False,
			active_complete = False,
			printer_token = "",
			client_name = "octoprint_AiPrintBox",
			client_key = "acGxgLJmvgTZU2RDZ3vQaiitxc5Bf6DDeHL1", #"b4943605-52b5-4d13-94ee-34eb983a813f"
			auto_start_print = True,
			mmf_print_complete = False,
			mmf_print_cancelled = False,
			bypass_bed_clear = False
		)

	def get_settings_version(self):
		return 1
		
	def on_settings_migrate(self, target, current=None):
		self._logger.debug("Settings migrate complete.")


	# ~~ EventHandlerPlugin API
	def on_event(self, event, payload):
		try:
			if event == Events.PRINT_STARTED:
				self._current_action_code = "101"
				self._settings.set_boolean(["mmf_print_complete"],False)
				self._settings.set_boolean(["mmf_print_cancelled"],False)
				self._settings.save()
				if not self._mmf_print:
					self._current_task_id = None
			elif event == Events.PRINT_DONE:
				if self._mmf_print and not self._settings.get_boolean(["bypass_bed_clear"]): # Send message back to UI to confirm clearing of bed.
					self._settings.set_boolean(["mmf_print_complete"],True)
					self._settings.save()
					self._plugin_manager.send_plugin_message(self._identifier, dict(mmf_print_complete=True))
				else:
					# self._current_action_code = "000"
					self._mmf_print = False
				self._current_action_code = "000"
			elif event == Events.PRINT_CANCELLED:
				if self._mmf_print and not self._settings.get_boolean(["bypass_bed_clear"]): # Send message back to UI to confirm clearing of bed.
					self._settings.set_boolean(["mmf_print_cancelled"],True)
					self._settings.save()
					self._plugin_manager.send_plugin_message(self._identifier, dict(mmf_print_cancelled=True))
				else:
					# self._current_action_code = "000"
					self._mmf_print = False
				self._current_action_code = "000"
			if event == Events.PRINT_PAUSED:
				self._current_action_code = "102"
				# self._current_action_code = "101"
			if event == Events.PRINT_RESUMED:
				self._current_action_code = "103"
			if event == Events.SLICING_STARTED:
				self._current_action_code = "301"
			if event == Events.SLICING_DONE:
				self._current_action_code = "302"	
			if event == Events.SLICING_FAILED:
				self._current_action_code = "303"	


			self._logger.info("receive info:" + str(event))
		except Exception as e:
			self._logger.info("on event error:" + str(e))
			self._plugin_manager.send_plugin_message(self._identifier,dict(error=str(e)))

	# ~~ StartupPlugin mixin

	def on_startup(self, host, port):
		self._port = port

		if self._settings.get_boolean(["mmf_print_complete"]) == False and self._settings.get_boolean(["mmf_print_cancelled"]) == False:
			self._current_action_code = "000"

		if not self._settings.get_boolean(["registration_complete"]):
			printInfo = dict(manufacturer = "CARS",model = "CARS-C8")
			self._on_regist_printer(printInfo)

		if self._settings.get_boolean(["registration_complete"]):
			self._on_active_printer()

		if self._settings.get_boolean(["active_complete"]):			
			self.mqtt_connect()
			self.on_after_startup()
		else:
			self._settings.set(["supported_printers"],self.get_supported_printers())
		
	def on_after_startup(self):
		if self._mqtt is None:
			return

		if self._settings.get_boolean(["active_complete"]) and self.mmf_status_updater is None:
			# start repeated timer publishing current status_code
			self.mmf_status_updater = RepeatedTimer(5,self.send_status)
#			self.send_status()
			self.mmf_status_updater.start()
			return

	# ~~ ShutdownPlugin mixin

	def on_shutdown(self):
		self.mqtt_disconnect(force=True)

	# ~~ AssetPlugin mixin

	def get_assets(self):
		return dict(
			js=["js/AiPrintBox.js"],
			css=["css/AiPrintBox.css"]
		)

	# ~~ SimpleApiPlugin mixin

	def get_api_commands(self):
		return dict(register_printer=["manufacturer","model"],forget_printer=[],mmf_print_complete=[])

	def _on_regist_printer(self , data):
		try:
    		# Generate serial number if it doesn't already exist.
			if self._settings.get(["printer_serial_number"]) == "":
				import uuid
				CM_UUID = str(uuid.uuid4())
				# CM_UUID = "2576c5ea-78c9-44b0-ab56-3ebd88cc4ac0"	

				self._settings.set(["printer_serial_number"],CM_UUID)
				import qrcode
				import image
				qrcodeStr = "{\"manufactor\":\"%s\",\"type\":\"%s\",\"name\":\"C8\",\"code\":\"%s\"}" % (data["manufacturer"],data["model"],CM_UUID)
				img = qrcode.make(qrcodeStr)
				current_work_dir = os.path.dirname(__file__)
				qrcode_path = os.path.join(current_work_dir, "static/images/cmid_qrcode.jpg")
				img.save(qrcode_path)
				

			url = "%sprinterInfo/registerPrinterInfo?printerCode=%s&manufactor=%s&type=%s" % (self.server_host, self._settings.get(["printer_serial_number"]),data["manufacturer"],data["model"])
			mac_address = ':'.join(("%012X" % get_mac())[i:i+2] for i in range(0, 12, 2))
			payload = "{\"manufacturer\": \"%s\",\"model\": \"%s\",\"firmware_version\": \"%s\",\"serial_number\": \"%s\",\"mac_address\": \"%s\"}" % (data["manufacturer"],data["model"],"1.0.0",self._settings.get(["printer_serial_number"]),mac_address)
			headers = {'X-Api-Key' : self._settings.get(["client_key"]),'Content-Type' : "application/json"}
			self._logger.debug("Sending data: %s with header: %s" % (payload,json.dumps(headers)))
			response = requests.post(url, data=payload, headers=headers)

			if response.status_code == 200:
				serialized_response = json.loads(response.text)
				self._logger.debug(json.dumps(serialized_response))
				self._settings.set(["printer_manufacturer"],data["manufacturer"])
				self._settings.set(["printer_model"],data["model"])
				self._settings.set(["printer_identifier"], self._identifier)
				self._settings.set_boolean(["registration_complete"], True)					
				self._settings.save()
			else:
				self._logger.info("API Error: %s" % response)
				self._plugin_manager.send_plugin_message(self._identifier, dict(error=response.status_code))
		except Exception as e:
			self._logger.info("regist printer error:" + str(e))
			self._plugin_manager.send_plugin_message(self._identifier,dict(error=str(e)))

	def _on_active_printer(self):
		try:
			serial_number = self._settings.get(["printer_serial_number"])
			if serial_number == "":
				self._plugin_manager.send_plugin_message(self._identifier, dict(error="The machine is not registered"))
				return
			url = "%sprinterInfo/findPrinterInfoTokenIdByPrinterCode?printerCode=%s" % (self.server_host, serial_number)
			payload = {}
			headers = {'X-Api-Key' : self._settings.get(["client_key"]),'Content-Type' : "application/json"}
			self._logger.debug("Sending data: %s with header: %s" % (payload,json.dumps(headers)))
			response = requests.get(url, data=payload, headers=headers)

			if response.status_code == 200:
				serialized_response = json.loads(response.text)
				self._logger.debug(json.dumps(serialized_response))
				data = serialized_response["data"]
				if data != None and len(data) > 0:
					self.mqtt_disconnect(force=True)
					self._settings.set(["printer_token"],serialized_response["data"])
					self._settings.set_boolean(["active_complete"], True)					
					self._settings.save()
					'''
					count = 10
					while count > 0:
						try:
							address = "localhost"
							port = 8025
							url = "http://%s:%s/%s" % (address,port,'updateResolv')
							requests.get(url)
							time.sleep(3)
							print(url)
							break
						except:
							count = count - 1
							continue
					'''
				else:
					self._settings.set_boolean(["active_complete"], False)	
			else:
				self._settings.set_boolean(["active_complete"], False)	
#			self._plugin_manager.send_plugin_message(self._identifier, dict(qr_image_url=serialized_response["qr_image_url"],printer_serial_number=self._settings.get(["printer_serial_number"])))
			self._plugin_manager.send_plugin_message(self._identifier, dict(printer_serial_number=self._settings.get(["printer_serial_number"])))
		except Exception as e:
			self._logger.info("active printer error: " + str(e))
			self._plugin_manager.send_plugin_message(self._identifier,dict(error=str(e)))

	def on_api_command(self, command, data):
		if not user_permission.can():
			return flask.make_response("Insufficient rights", 403)

		if command == "register_printer":	
			"""
			if "manufacturer" not in data:
				data["manufacturer"] = "cars"
			if "model" not in data:
				data["model"] = "fdm-printer"
			"""			
			self._on_regist_printer(data)	

		if command == "forget_printer":
			# new_supported_printers = self.get_supported_printers()
			self.mqtt_disconnect(force=True)
			self._settings.set(["printer_serial_number"],"")
			self._settings.set(["printer_token"],"")
			self._settings.set_boolean(["registration_complete"], False)
			# self._settings.set(["supported_printers"],new_supported_printers)
			self._settings.save()
			# self._plugin_manager.send_plugin_message(self._identifier, dict(printer_removed=True))
			return flask.jsonify({"printer_removed":True}) #,"supported_printers":new_supported_printers})
			
		if command == "mmf_print_complete":
			self._mmf_print = False
			self._current_action_code = "000"
			self._settings.set_boolean(["mmf_print_complete"],False)
			self._settings.set_boolean(["mmf_print_cancelled"],False)
			self._settings.save()
			return flask.jsonify(bed_cleared=True)

	# ~~ PrinterCallback
	def on_printer_add_temperature(self, data):
		if self._settings.get_boolean(["active_complete"]):
			# self._logger.info("add temperature %s" % data)
			if data.get("tool0"):
				self._current_temp_hotend = data["tool0"]["actual"]
			if data.get("bed"):
				self._current_temp_bed = data["bed"]["actual"]

	# ~~ AiPrintBox Functions
	def get_supported_printers(self):
		url = "%sprinterInfo/supportedPrinters" % (self.server_host)
		headers = {'X-Api-Key': self._settings.get(["client_key"])}
		response = requests.get(url, headers=headers)
		if response.status_code == 200:
			self._logger.debug("Received printers: %s" % response.text)
			filtered_printers = json.loads(response.text)["items"]
			return filtered_printers
		else:
			self._logger.debug("Error getting printers: %s" % response)

	def send_status(self):
#		self.mmf_status_updater.cancel()
		printer_disconnected = self._printer.is_closed_or_error()
		if not printer_disconnected:
			printer_token = self._settings.get(["printer_token"]),
			topic = "/printers/%s/client/status" % printer_token
			printer_data = self._printer.get_current_data()
			# self._logger.info(printer_data)

			message = dict(actionCode = 300,
						   status = self._get_current_status(),
						   printer_token = printer_token,
						   manufacturer = self._settings.get(["printer_manufacturer"]),
						   model = self._settings.get(["printer_model"]),
						   firmware_version = self._settings.get(["printer_firmware_version"]),
						   serial_number = self._settings.get(["printer_serial_number"]),
						   current_task_id = self._current_task_id,
						   temperature = "%s" % self._current_temp_hotend,
						   bed_temperature = "%s" % self._current_temp_bed,
						   print_progress = printer_data["progress"],
						   #print_time = int(printer_data["progress"]["printTime"] or 0),
						   #print_progress = int(printer_data["progress"]["completion"] or 0),
						   #remaining_time = int(printer_data["progress"]["printTimeLeft"] or 0),
						   #total_time = int(printer_data["job"]["estimatedPrintTime"] or 0),
						   date = self._get_timestamp()
						   ) 

			self._logger.debug(message)
			self.mqtt_publish(topic,message)
		self._logger.info('send status: ' + self._get_current_status())
#		if not self.mmf_status_updater._timer_active():
#			self.mmf_status_updater.start()

	def _get_current_status(self):
		return self._printer_status[self._current_action_code]

	def _get_timestamp(self):
		timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
		return timestamp
		
	# ~~ Printer Action Functions
	def _download_file(self, data,pub_topic,restapi,message):
		try:
			# Make API call to AiPrintBox to download gcode file.
	#		payload = dict(file_id = action["file_id"],printer_token = self._settings.get(["printer_token"]))
			action = json.loads(data)
			url = action["filePath"]
			headers = {'X-Api-Key': self._settings.get(["client_key"])}
			fileName = action["fileName"]

			self._current_action_code = "201"
			payload = "key=%s" % action["key"]

			response = requests.get(url, params=payload, headers=headers)

			self._logger.debug("Sending parameters: %s with header: %s" % (payload,headers))
			if response.status_code == 200:
				# Save file to uploads folder
				sanitize_file_name = self._file_manager.sanitize_name("local",fileName)
				download_file = "%s/%s" % (self._settings.global_get_basefolder("uploads"),sanitize_file_name)
				self._logger.debug("Saving file: %s" % download_file)

				with open(download_file, 'wb') as f:
					f.write(response.content)
					f.close()

				if download_file.endswith(".obj"):					
					mesh = trimesh.load_mesh(download_file)
					stl_download_file = download_file.replace(".obj",".stl")
					mesh.export(stl_download_file,"stl_ascii")
					os.remove(download_file)

				state = dict(status_code = response.status_code,text = "successful")
			else:
				self._logger.debug("API Error: %s" % response)
				self._plugin_manager.send_plugin_message(self._identifier, dict(error=response.status_code))
				state = dict(status_code = response.status_code,text = response.text)
			
		except Exception as e:
			self._logger.info("download file error :"+ str(e))
			self._plugin_manager.send_plugin_message(self._identifier,dict(error = str(e)))
			state = dict(status_code = 400,text = str(e))

		self._current_action_code = "000"
		self.mqtt_publish("%s/%s/status" % (pub_topic,restapi), state["status_code"])
		self.mqtt_publish("%s/%s/response" % (pub_topic,restapi), state["text"])
		self._plugin_manager.send_plugin_message(self._identifier, dict(topic=restapi,message=message,subscribecommand="Status code: %s" % state["status_code"]))
		
#		return dict(status_code = 400,text = str(e))
	# ~~ MQTT Functions
	def mqtt_connect(self):
		# broker_url = "mqtt.AiPrintBox.com"
		# broker_username = self._settings.get(["client_name"])
		# broker_password = self._settings.get(["client_key"])

		broker_url = "139.159.151.243"
		broker_username = "cars"
		broker_password = "cars"

		# broker_insecure_port = 1883
		broker_insecure_port = 5059
		broker_tls_port = 8883
		broker_port = broker_insecure_port
		broker_keepalive = 60
		use_tls = False
		broker_tls_insecure = False # may need to set this to true

		import paho.mqtt.client as mqtt

		broker_protocol = mqtt.MQTTv31

		if self._mqtt is None:
			self._mqtt = mqtt.Client(protocol=broker_protocol)

		if broker_username is not None:
		 	self._mqtt.username_pw_set(broker_username, password=broker_password)

		if use_tls and not self._mqtt_tls_set:
			self._mqtt.tls_set() # Uses the default certification authority of the system https://pypi.org/project/paho-mqtt/#tls-set
			self._mqtt_tls_set = True

		if broker_tls_insecure and not self._mqtt_tls_set:
			self._mqtt.tls_insecure_set(broker_tls_insecure)
			broker_port = broker_insecure_port # Fallbacks to the non-secure port 1883

		self._mqtt.on_connect = self._on_mqtt_connect
		self._mqtt.on_disconnect = self._on_mqtt_disconnect
		self._mqtt.on_message = self._on_mqtt_message

		self._mqtt.connect_async(broker_url, broker_port, keepalive=broker_keepalive)
		if self._mqtt.loop_start() == mqtt.MQTT_ERR_INVAL:
			self._logger.error("Could not start MQTT connection, loop_start returned MQTT_ERR_INVAL")

	def mqtt_disconnect(self, force=False):
		if self._mqtt is None:
			return

		self._mqtt.loop_stop()

		if force:
			time.sleep(1)
			self._mqtt.loop_stop(force=True)
			if self.mmf_status_updater:
				self._logger.debug("Stopping MQTT status updates.")
				self.mmf_status_updater.cancel()

		self._logger.debug("Disconnected from AiPrintBox.")

	def mqtt_publish(self, topic, payload, retained=False, qos=0):
		if not isinstance(payload, basestring):
			payload = json.dumps(payload)

		if self._mqtt_connected:
			self._mqtt.publish(topic, payload=payload, retain=retained, qos=qos)
			# self._logger.debug("Sent message: {topic} - {payload}".format(**locals()))
			return True
		else:
			return False

	def _on_mqtt_subscription(self, topic, message, retained=None, qos=None, *args, **kwargs):

		action = json.loads(message)

		try:
			settings = octoprint.settings.Settings()
			api_key = settings.get(["api", "key"])
			address = "localhost"
			port = self._port
			restapi = action["act_restapi"]
			url = "http://%s:%s/api/%s" % (address,port,restapi)

			self._logger.debug("Received from " + topic + "|" + str(message))

			# content_type = base64.b64decode(action["act_content-type"]).encode("utf-8")
			content_type = action["act_content-type"]

			headers = {'Content-type': content_type, 'X-Api-Key': api_key}
			# headers = {'Content-type': 'application/json','X-Api-Key': api_key}

			pub_topic = "/printers/%s/client" % self._settings.get(["printer_token"])

			# OctoPrint RestAPI
			if action["act_type"] == "post":
				data = base64.b64decode(action["act_cmd"])
				r = requests.post(url, data=data, headers=headers)
				self.mqtt_publish("%s/%s/status" % (pub_topic,restapi), r.status_code)
				self.mqtt_publish("%s/%s/response" % (pub_topic,restapi), r.text)
				self._plugin_manager.send_plugin_message(self._identifier, dict(topic=restapi,message=message,subscribecommand="Status code: %s" % r.status_code))			
			if action["act_type"] == "get":
				r = requests.get(url, headers=headers)
				self.mqtt_publish("%s/%s/status" % (pub_topic,restapi), r.status_code)
				self.mqtt_publish("%s/%s/response" % (pub_topic,restapi), r.text)
				self._plugin_manager.send_plugin_message(self._identifier, dict(topic=restapi,message=message,subscribecommand="Response: %s" % r.text))

			# download file from BIM System	
			if action["act_type"] == "download":
				data = base64.b64decode(action["act_cmd"])		
				_thread.start_new_thread(self._download_file,(data,pub_topic,restapi,message,))

			# reset access point command
			if action["act_type"] == "resetap":
				self.mqtt_publish("%s/%s/response" % (pub_topic,restapi),'reset access point. please wait a moment......')
				url = "http://%s:%s/%s" % (address,8025,'setHotPoint')
				r = requests.get(url, headers=headers)

			if action["act_type"] == "update_resolv":
				self.mqtt_publish("%s/%s/response" % (pub_topic,restapi),'update resolv configuration......')
				url = "http://%s:%s/%s" % (address,8025,'updateResolv')
				r = requests.get(url, headers=headers)

			if action["act_type"] == "delete":
				r = requests.delete(url,headers = headers)
				self.mqtt_publish("%s/%s/status" % (pub_topic,restapi), r.status_code)
				self.mqtt_publish("%s/%s/response" % (pub_topic,restapi), r.text)
				self._plugin_manager.send_plugin_message(self._identifier, dict(topic=restapi,message=message,subscribecommand="Response: %s" % r.text))

		except Exception as e:
			self.mqtt_publish("%s/%s/response" % (pub_topic,restapi),str(e) )
			self._logger.info("subscription message error:" + str(e))
			self._plugin_manager.send_plugin_message(self._identifier, dict(message=str(e)))

	def _on_mqtt_connect(self, client, userdata, flags, rc):
		if not client == self._mqtt:
			return

		if not rc == 0:
			reasons = [
				None,
				"Connection to AiPrintBox refused, wrong protocol version",
				"Connection to AiPrintBox refused, incorrect client identifier",
				"Connection to AiPrintBox refused, server unavailable",
				"Connection to AiPrintBox refused, bad username or password",
				"Connection to AiPrintBox refused, not authorised"
			]

			if rc < len(reasons):
				reason = reasons[rc]
			else:
				reason = None

			self._logger.error(reason if str(reason) else "Connection to AiPrintBox broker refused, unknown error")
			return

	#	self._logger.info("Connected to AiPrintBox")

		printer_actived = self._settings.get_boolean(["active_complete"])
		if printer_actived:
			self._mqtt.subscribe("/printers/%s/controller" % self._settings.get(["printer_token"]))
			self._logger.info("Subscribed to AiPrintBox printer topic:%s" % self._settings.get(["printer_token"]))

		self._mqtt_connected = True

	def _on_mqtt_disconnect(self, client, userdata, rc):
		if not client == self._mqtt:
			return

		self._logger.info("Disconnected from AiPrintBox.")

	def _on_mqtt_message(self, client, userdata, msg):
		if not client == self._mqtt:
			return

		from paho.mqtt.client import topic_matches_sub
		if topic_matches_sub("/printers/%s/controller" % self._settings.get(["printer_token"]), msg.topic):
			args = [msg.topic, msg.payload]
			kwargs = dict(retained=msg.retain, qos=msg.qos)
			try:
				self._on_mqtt_subscription(*args, **kwargs)
			except:
				self._logger.exception("Error while calling AiPrintBox callback")

	# ~~ Softwareupdate hook
	def get_update_information(self):
		return dict(
			AiPrintBox=dict(
				displayName="AiPrintBox",
				displayVersion=self._plugin_version,
				type="github_release",
				user="springtiger",
				repo="OctoPrint-AiPrintBox",
				current=self._plugin_version,
				pip="https://github.com/springtiger/aiprintbox/archive/{target_version}.zip"				
			)
		)


__plugin_name__ = "AiPrintBox"
__plugin_pythoncompat__ = ">=2.7,<4"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = AiPrintBoxPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
	}

