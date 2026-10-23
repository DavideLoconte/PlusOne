// Quick Settings toggle for Plus One. All the work happens in the Plus One
// application; this only talks to it over D-Bus (and starts it on demand
// through D-Bus activation).
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'io.github.gabrielesantangelo.PlusOne';
const OBJECT_PATH = '/io/github/gabrielesantangelo/PlusOne';
const PlusOneProxy = Gio.DBusProxy.makeProxyWrapper(`
<node>
  <interface name="io.github.gabrielesantangelo.PlusOne1">
    <method name="Connect"/>
    <method name="Disconnect"/>
    <method name="ShowWindow"/>
    <property name="State" type="s" access="read"/>
    <property name="Active" type="b" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Detail" type="s" access="read"/>
    <property name="Background" type="b" access="readwrite"/>
  </interface>
</node>`);

// The application translates its own state texts; these are the only others.
const ITALIAN = {
    'Off': 'Spento',
    'Ready in the background': 'Pronto in background',
    'Settings': 'Impostazioni',
};
const italian = GLib.get_language_names()[0].startsWith('it');
const _ = text => (italian && ITALIAN[text]) || text;

const PlusOneToggle = GObject.registerClass(
class PlusOneToggle extends QuickMenuToggle {
    _init(proxy) {
        super._init({title: 'Tablet', iconName: 'tablet-symbolic', toggleMode: false});
        this._proxy = proxy;
        this.connect('clicked', () => {
            const call = proxy.Active ? proxy.DisconnectAsync() : proxy.ConnectAsync();
            call.catch(logError);
        });

        this.menu.setHeader('tablet-symbolic', 'Plus One');
        this._background = new PopupMenu.PopupSwitchMenuItem(_('Ready in the background'), false);
        this._background.connect('toggled', (_item, state) => {
            if (state !== Boolean(proxy.Background))
                proxy.Background = state;
        });
        this.menu.addMenuItem(this._background);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction(_('Settings'), () => {
            proxy.ShowWindowAsync().catch(logError);
            Main.panel.closeQuickSettings();
        });
    }

    sync() {
        const running = this._proxy.g_name_owner !== null;
        this.checked = running && Boolean(this._proxy.Active);
        this.subtitle = running ? this._proxy.Title : _('Off');
        this.menu.setHeader('tablet-symbolic', 'Plus One', running ? this._proxy.Detail : null);
        this._background.setToggleState(running && Boolean(this._proxy.Background));
    }
});

const PlusOneIndicator = GObject.registerClass(
class PlusOneIndicator extends SystemIndicator {
    _init() {
        super._init();
        this._icon = this._addIndicator();
        this._icon.iconName = 'tablet-symbolic';
        this._icon.visible = false;
        this._cancellable = new Gio.Cancellable();
        // Do not start Plus One just because the shell came up: only a click does.
        new PlusOneProxy(Gio.DBus.session, BUS_NAME, OBJECT_PATH, (proxy, error) => {
            if (error) {
                if (!error.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                    logError(error, 'Plus One');
                return;
            }
            this._proxy = proxy;
            this._toggle = new PlusOneToggle(proxy);
            this.quickSettingsItems.push(this._toggle);
            this._signals = [
                proxy.connect('g-properties-changed', () => this._sync()),
                proxy.connect('notify::g-name-owner', () => this._sync()),
            ];
            this._sync();
            Main.panel.statusArea.quickSettings.addExternalIndicator(this);
        }, this._cancellable, Gio.DBusProxyFlags.DO_NOT_AUTO_START_AT_CONSTRUCTION);
    }

    _sync() {
        this._toggle.sync();
        this._icon.visible = this._proxy.g_name_owner !== null && this._proxy.State === 'streaming';
    }

    destroy() {
        this._cancellable.cancel();
        this._signals?.forEach(id => this._proxy.disconnect(id));
        this.quickSettingsItems.forEach(item => item.destroy());
        super.destroy();
    }
});

export default class PlusOneExtension extends Extension {
    enable() {
        this._indicator = new PlusOneIndicator();
    }

    disable() {
        this._indicator.destroy();
        this._indicator = null;
    }
}
