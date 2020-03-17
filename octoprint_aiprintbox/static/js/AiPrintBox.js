/*
 * View model for OctoPrint-AiPrintBox
 *
 * Author: jneilliii
 * License: AGPLv3
 */
$(function() {
	function AiPrintBoxViewModel(parameters) {
		var self = this;
		
		// add icon to tab
		$('li#tab_plugin_aiprintbox_link > a').html('<div class="aiprintbox_logo"></div>AiPrintBox');

		self.loginStateViewModel = parameters[0];
		self.settingsViewModel = parameters[1];

		self.printer_model = ko.observable();
		self.printer_manufacturer = ko.observable();
		self.printer_serial_number = ko.observable();
		self.supported_printers = ko.observableArray();
		self.printer_token = ko.observable();
		self.registering = ko.observable(false);
		self.forgetting = ko.observable(false);
		self.registration_complete = ko.observable();
		self.qr_image_url = ko.observable('');
		self.mmf_print_complete = ko.observable();
		self.mmf_print_cancelled = ko.observable();
		self.bypass_bed_clear = ko.observable();
		self.supported_manufacturers = ko.computed(function() {
				var seen =[];
				return ko.utils.arrayFilter(self.supported_printers(), function(item) {
						return seen.indexOf(item.brand()) == -1 && seen.push(item.brand());
					}).sort(function (left, right) { return left.brand() == right.brand() ? 0 : (left.brand() < right.brand() ? -1 : 1) });
			});
		self.supported_printers_filtered = ko.computed(function(){
			if (!self.printer_manufacturer()) {
				return self.supported_printers();
			} else {
				return ko.utils.arrayFilter(self.supported_printers(), function(item) {
						return (item.brand() == self.printer_manufacturer());
					});
			}
		});

		self.onBeforeBinding = function() {
			self.registration_complete(self.settingsViewModel.settings.plugins.AiPrintBox.registration_complete());
			//self.supported_printers(self.settingsViewModel.settings.plugins.AiPrintBox.supported_printers());
			self.printer_model(self.settingsViewModel.settings.plugins.AiPrintBox.printer_model());
			self.printer_manufacturer(self.settingsViewModel.settings.plugins.AiPrintBox.printer_manufacturer());
			self.printer_serial_number(self.settingsViewModel.settings.plugins.AiPrintBox.printer_serial_number());
			self.printer_token(self.settingsViewModel.settings.plugins.AiPrintBox.printer_token());
			self.mmf_print_complete(self.settingsViewModel.settings.plugins.AiPrintBox.mmf_print_complete());
			self.mmf_print_cancelled(self.settingsViewModel.settings.plugins.AiPrintBox.mmf_print_cancelled());
			self.bypass_bed_clear(self.settingsViewModel.settings.plugins.AiPrintBox.bypass_bed_clear());
			$.ajax({
				type: 'GET',
				url: "https://www.myminifactory.com/api/v2/printers?automatic_slicing=1&per_page=-1",
				headers: {
					"X-Api-Key": "acGxgLJmvgTZU2RDZ3vQaiitxc5Bf6DDeHL1",
				}
			}).done(function (data) {
				console.log(data);
				var new_supported_printers = ko.utils.arrayMap(data.items,function(item){
					var new_item = {}
					for (x in item){
						new_item[x] = ko.observable(item[x]);
					}
					return new_item;
				});
				self.supported_printers(new_supported_printers);
				self.forgetting(false);
				$("#AiPrintBoxForgetWarning").modal("hide");
			});
		}
		
		self.onAfterBinding = function() {
			if(self.mmf_print_complete()){
				self.onDataUpdaterPluginMessage('AiPrintBox',{'mmf_print_complete':true});
			}
			if(self.mmf_print_cancelled()){
				self.onDataUpdaterPluginMessage('AiPrintBox',{'mmf_print_cancelled':true});
			}
		}
		
		self.onSettingsHidden = function() {
			if(self.registration_complete()){
				self.qr_image_url('');
			}
		}

		self.onEventSettingsUpdated = function(payload) {
			self.supported_printers(self.settingsViewModel.settings.plugins.AiPrintBox.supported_printers());
			self.printer_model(self.settingsViewModel.settings.plugins.AiPrintBox.printer_model());
			self.printer_manufacturer(self.settingsViewModel.settings.plugins.AiPrintBox.printer_manufacturer());
			self.printer_serial_number(self.settingsViewModel.settings.plugins.AiPrintBox.printer_serial_number());
			self.printer_token(self.settingsViewModel.settings.plugins.AiPrintBox.printer_token());
			self.mmf_print_complete(self.settingsViewModel.settings.plugins.AiPrintBox.mmf_print_complete());
			self.mmf_print_cancelled(self.settingsViewModel.settings.plugins.AiPrintBox.mmf_print_cancelled());
			self.bypass_bed_clear(self.settingsViewModel.settings.plugins.AiPrintBox.bypass_bed_clear());
		}
		
		self.onSettingsBeforeSave = function() {
			self.settingsViewModel.settings.plugins.AiPrintBox.supported_printers(self.supported_printers());
			self.settingsViewModel.settings.plugins.AiPrintBox.printer_model(self.printer_model());
			self.settingsViewModel.settings.plugins.AiPrintBox.printer_manufacturer(self.printer_manufacturer());
			self.settingsViewModel.settings.plugins.AiPrintBox.printer_serial_number(self.printer_serial_number());
			self.settingsViewModel.settings.plugins.AiPrintBox.printer_token(self.printer_token());
			self.settingsViewModel.settings.plugins.AiPrintBox.bypass_bed_clear(self.bypass_bed_clear());
		}

		self.onDataUpdaterPluginMessage = function(plugin, data) {
			if (plugin != "AiPrintBox") {
				return;
			}

			if(data.error) {
				self.registering(false);
				new PNotify({
						title: 'AiPrintBox Error',
						type: 'error',
						text: '<div class="row-fluid"><p>Ther was an error with the AiPrintBox plugin, error details follow.</p><br/></div><p><pre style="padding-top: 5px;">'+data.error+'</pre></p>',
						hide: false
						});	
				return;
			}

			if(data.qr_image_url && data.qr_image_url !== self.qr_image_url()) {
				console.log(data.qr_image_url);
				self.registering(false);
				self.qr_image_url(data.qr_image_url);
				self.printer_serial_number(data.printer_serial_number);
				self.registration_complete(true);
				return;
			}
			
			if(data.printer_removed) {
				self.qr_image_url('');
				self.registration_complete(false);
				self.forgetting(false);
				self.printer_serial_number('');
				$("#AiPrintBoxForgetWarning").modal("hide");
			}
			
			if(data.mmf_print_complete || data.mmf_print_cancelled){
				self.notify = new PNotify({
						title: 'AiPrintBox Click and Print',
						type: 'info',
						text: '<div class="row-fluid" style="padding-top: 20px;"><p>AiPrintBox Click and Print job ' + (data.mmf_print_complete ? 'complete' : 'cancelled') + '. Please clear the bed and press Ok below to free up the printer again.</p></div>',
						hide: false,
						buttons: {
							closer: false,
							sticker: false
						},
						confirm: {
							confirm: true,
							buttons: [{
								text: 'Ok',
								addClass: 'btn-primary',
								click: function(notice) {
									$.ajax({
										url: API_BASEURL + "plugin/aiprintbox",
										type: "POST",
										dataType: "json",
										data: JSON.stringify({
											command: "mmf_print_complete"
										}),
										contentType: "application/json; charset=UTF-8"
									}).done(function(data){
												if(data.bed_cleared){
													notice.remove();
													self.mmf_print_complete(true);
												}
											});
								}
							},{addClass: 'hidden'}]
						}
						});
			}
		}
		
		self.onTabChange = function(current, previous) {
				if (current === "#tab_plugin_aiprintbox") {
					$('#aiprintbox_iframe').attr('src','https://www.AiPrintBox.com/');
				} else if (previous === "#tab_plugin_aiprintbox") {
					$('#aiprintbox_iframe').attr('src','');
				}
			};

		// Utility Functions
		self.get_qr_image_url = function(){
			console.log('Registering printer with AiPrintBox');
			self.registering(true);
			$.ajax({
				url: API_BASEURL + "plugin/aiprintbox",
				type: "POST",
				dataType: "json",
				data: JSON.stringify({
					command: "register_printer",
					manufacturer: self.printer_manufacturer(),
					model: self.printer_model()
				}),
				contentType: "application/json; charset=UTF-8"
			});
		}
		
		self.cancelClick = function(data) {
			self.forgetting(false);
		}
		
		self.confirm_forget = function(){
			self.forgetting(true);
			$("#AiPrintBoxForgetWarning").modal("show");
		}

		self.forget_registration = function(){
			console.log('Removing configured printer locally.');
			$.ajax({
				url: API_BASEURL + "plugin/aiprintbox",
				type: "POST",
				dataType: "json",
				data: JSON.stringify({
					command: "forget_printer"
				}),
				contentType: "application/json; charset=UTF-8"
			}).done(function(response){
				if (response.printer_removed){
					console.log('Printer forgotten, reloading list of supported printers.');
					//console.log(response.supported_printers);
					$.ajax({
						type: 'GET',
						url: "https://www.MyMinifactory.com/api/v2/printers?automatic_slicing=1&per_page=-1",
						headers: {
							"X-Api-Key": "acGxgLJmvgTZU2RDZ3vQaiitxc5Bf6DDeHL1",
						}
					}).done(function (data) {
						console.log(data);
						self.qr_image_url('');
						self.registration_complete(false);
						self.printer_serial_number('');
						var new_supported_printers = ko.utils.arrayMap(data,function(item){
							var new_item = {}
							for (x in item){
								new_item[x] = ko.observable(item[x]);
							}
							return new_item;
						});
						self.supported_printers(new_supported_printers);
						self.forgetting(false);
						$("#AiPrintBoxForgetWarning").modal("hide");
					});
				}
			});
		}
	}

	OCTOPRINT_VIEWMODELS.push({
		construct: AiPrintBoxViewModel,
		dependencies: ["loginStateViewModel","settingsViewModel"],
		elements: ["#tab_plugin_aiprintbox","#settings_plugin_aiprintbox"]
	});
});
