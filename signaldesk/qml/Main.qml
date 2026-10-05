import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: root
    width: 1380; height: 900
    minimumWidth: 1080; minimumHeight: 720
    visible: true
    title: "Gold Signal Desk • إشارات الذهب"
    color: "#0c1217"
    palette.window: "#121d25"
    palette.windowText: "#e6edf0"
    palette.base: "#0f181f"
    palette.text: "#e6edf0"
    palette.button: "#1a242c"
    palette.buttonText: "#e6edf0"
    palette.highlight: "#c9ac70"
    palette.highlightedText: "#182018"
    property int page: 0
    property var d: bridge.data
    property var cfg: JSON.parse(JSON.stringify(bridge.settings))
    property bool dirty: false
    property var channelOptions: []
    property var symbolOptions: []
    property string symbolSearch: ""
    property var filteredSymbols: symbolOptions.filter(s => !symbolSearch.trim() || (s.name + " " + (s.description || "")).toLowerCase().includes(symbolSearch.trim().toLowerCase()))
    property double selectedChannelId: cfg.channel_id || 0
    property string accountContext: ""
    property string message: ""
    property color accent: "#c9ac70"
    property color green: "#76cfb2"
    property color muted: "#8a9ba7"
    property var titles: ["نظرة عامة", "الصفقات", "الإشارات المنتظرة", "التقارير", "سجل النشاط", "الإعدادات", "الاتصالات", "مختبر المحاكاة"]
    font.family: "Segoe UI"
    font.pixelSize: 14

    function send(action, values) { bridge.send(JSON.stringify(Object.assign({action: action}, values || {}))) }
    function setCfg(key, value) { let next = Object.assign({}, cfg); next[key] = value; cfg = next; dirty = true }
    function decimalValue(text) {
        let value = String(text).trim().replace(/[٠-٩]/g, c => String(c.charCodeAt(0) - 0x660))
            .replace(/[۰-۹]/g, c => String(c.charCodeAt(0) - 0x6f0)).replace(/[٫،,]/g, ".")
        return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value) && isFinite(Number(value)) ? Number(value) : null
    }
    function number(value, decimals) { return value === undefined || value === null ? "—" : Number(value).toFixed(decimals === undefined ? 2 : decimals) }
    function stateName(state) { return ({open:"مفتوحة",closed:"مغلقة",pending:"بانتظار السعر",expired:"انتهت المهلة",cancelled:"ملغاة",rejected:"مرفوضة",uncertain:"تحتاج مطابقة",sending:"جارٍ التأكيد"})[state] || state }
    function timeText(value) { return new Date(value * 1000).toLocaleString(Qt.locale("ar"), "MM/dd hh:mm:ss") }
    function currentSignals(state) { return (d.signals || []).filter(s => !state || s.state === state) }
    function realized() { return (d.signals || []).reduce((sum,s) => sum + (s.realized || 0), 0) }
    function floating() { return currentSignals("open").reduce((sum,s) => sum + (s.profit || 0), 0) }
    function saveConfig(cancelPrevious) {
        bridge.saveSettings(JSON.stringify({settings:cfg, cancel_previous:cancelPrevious || false})); dirty = false
    }
    function updateConnectionModels() {
        let channels = d.channels || []
        if (JSON.stringify(channelOptions) !== JSON.stringify(channels)) {
            channelOptions = channels
        }
        let symbols = d.symbols || []
        if (JSON.stringify(symbolOptions) !== JSON.stringify(symbols)) symbolOptions = symbols
    }
    Component.onCompleted: { accountContext = d.account_id || ""; updateConnectionModels() }
    Connections {
        target: bridge
        function onChanged() {
            let account = root.d.account_id || ""
            if (account && account !== root.accountContext) {
                root.contentItem.forceActiveFocus()
                root.accountContext = account
                enableDialog.close()
                root.message = ""
                toastTimer.stop()
                root.dirty = false
                root.cfg = JSON.parse(JSON.stringify(bridge.settings))
                root.selectedChannelId = root.cfg.channel_id || 0
                root.symbolSearch = ""
            }
            root.updateConnectionModels()
        }
        function onSettingsChanged() {
            if (!root.dirty) {
                let next = JSON.parse(JSON.stringify(bridge.settings))
                if (next.channel_id !== root.cfg.channel_id) {
                    root.selectedChannelId = next.channel_id
                }
                root.cfg = next
            }
        }
        function onToast(text) { root.message = text; toastTimer.restart() }
    }
    Timer { id: toastTimer; interval: 6500; onTriggered: root.message = "" }

    component LabelText: Text {
        color: "#e6edf0"; font.family: "Segoe UI"; font.pixelSize: 14
        horizontalAlignment: Text.AlignRight; verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    component Action: Button {
        id: actionControl
        property bool primary: false
        property bool danger: false
        implicitHeight: 42
        leftPadding: 18; rightPadding: 18
        font.family: "Segoe UI"; font.pixelSize: 14
        contentItem: Text { text: actionControl.text; font: actionControl.font; color: actionControl.primary ? "#182018" : actionControl.danger ? "#efa09b" : "#e6edf0"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 9; color: actionControl.primary ? (actionControl.down ? "#b79b61" : root.accent) : actionControl.hovered ? "#27323b" : "#1a242c"; border.color: actionControl.primary ? root.accent : "#2a3640"; opacity: actionControl.enabled ? 1 : .4 }
    }
    component Card: Rectangle {
        color: "#131d25"; radius: 14; border.color: "#25313a"
        implicitHeight: 148
    }
    component Input: TextField {
        id: field
        color: "#edf1f3"; selectionColor: "#517c6e"; placeholderTextColor: "#687a87"
        implicitHeight: 42; font.family: "Segoe UI"; font.pixelSize: 14
        horizontalAlignment: Text.AlignRight
        leftPadding: 12; rightPadding: 12
        background: Rectangle { radius: 8; color: "#0d161e"; border.color: field.activeFocus ? root.accent : "#2b3943" }
    }
    component DecimalInput: Input {
        id: decimalField
        property var numericValue
        signal numberEdited(var value)
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        // Updating cfg must not replace an in-progress value such as "0." or "0.0".
        Binding {
            target: decimalField; property: "text"
            value: String(decimalField.numericValue)
            when: !decimalField.activeFocus
            restoreMode: Binding.RestoreNone
        }
        onTextEdited: {
            let value = root.decimalValue(text)
            // Preserve invalid/unfinished text so saving rejects it instead of using an old value.
            numberEdited(value === null ? text : value)
        }
    }
    component Numeric: ColumnLayout {
        id: numericSetting
        property string label
        property string settingKey
        Layout.fillWidth: true
        spacing: 7
        LabelText { text: parent.label; color: root.muted; Layout.fillWidth: true }
        DecimalInput { objectName: "numeric_" + numericSetting.settingKey; Layout.fillWidth: true; numericValue: root.cfg[numericSetting.settingKey]; onNumberEdited: value => root.setCfg(numericSetting.settingKey, value) }
    }
    component Choice: ColumnLayout {
        property string label
        property string settingKey
        property var labels: []
        property var values: []
        Layout.fillWidth: true
        spacing: 7
        LabelText { text: parent.label; color: root.muted; Layout.fillWidth: true }
        ComboBox {
            id: combo; Layout.fillWidth: true; implicitHeight: 42
            model: parent.labels; currentIndex: Math.max(0, parent.values.indexOf(root.cfg[parent.settingKey]))
            onActivated: root.setCfg(parent.settingKey, parent.values[index])
            palette.button: "#0d161e"; palette.buttonText: "#edf1f3"; palette.text: "#edf1f3"; palette.base: "#17242e"; palette.highlight: "#496e62"
        }
    }
    component Toggle: RowLayout {
        property string label
        property string settingKey
        Layout.fillWidth: true
        Switch { checked: Boolean(root.cfg[parent.settingKey]); palette.highlight: root.green; onToggled: root.setCfg(parent.settingKey, checked) }
        LabelText { text: parent.label; Layout.fillWidth: true; wrapMode: Text.Wrap; elide: Text.ElideNone }
    }
    component Metric: Card {
        property string label
        property string value
        property string subtitle
        property color valueColor: "#edf1f3"
        Layout.fillWidth: true; implicitHeight: 142
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 20; spacing: 10
            LabelText { text: parent.parent.label; color: root.muted; Layout.fillWidth: true }
            LabelText { text: parent.parent.value; color: parent.parent.valueColor; font.pixelSize: 30; font.weight: Font.DemiBold; Layout.fillWidth: true }
            LabelText { text: parent.parent.subtitle; color: "#657b89"; font.pixelSize: 12; Layout.fillWidth: true }
        }
    }
    component SectionTitle: LabelText { font.pixelSize: 18; font.weight: Font.DemiBold; Layout.fillWidth: true }
    component Empty: ColumnLayout {
        Layout.fillWidth: true; Layout.minimumHeight: 190
        property string heading: "لا توجد بيانات بعد"
        property string detail: "ستظهر البيانات هنا عند وصول الإشارات"
        spacing: 12
        LabelText { text: "◇"; color: root.accent; font.pixelSize: 34; Layout.alignment: Qt.AlignHCenter }
        LabelText { text: parent.heading; Layout.alignment: Qt.AlignHCenter; font.pixelSize: 17 }
        LabelText { text: parent.detail; color: root.muted; Layout.alignment: Qt.AlignHCenter }
    }

    Rectangle {
        id: sidebar
        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: 234; color: "#111a21"
        Rectangle { anchors.left: parent.left; width: 1; height: parent.height; color: "#243039" }
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 20; spacing: 8
            Rectangle {
                Layout.alignment: Qt.AlignRight; width: 48; height: 48; radius: 13; color: "#c9ac70"
                Text { anchors.centerIn: parent; text: "G"; font.pixelSize: 30; font.weight: Font.Bold; color: "#15241c" }
            }
            LabelText { text: "إشارات الذهب"; font.pixelSize: 23; font.weight: Font.DemiBold; Layout.fillWidth: true; Layout.topMargin: 9 }
            LabelText { text: "GOLD SIGNAL DESK"; color: root.muted; font.pixelSize: 10; font.letterSpacing: 1.5; Layout.fillWidth: true; Layout.bottomMargin: 25 }
            Repeater {
                model: root.titles
                delegate: Rectangle {
                    required property int index
                    required property string modelData
                    Layout.fillWidth: true; height: 46; radius: 9
                    color: root.page === index ? "#26342e" : navMouse.containsMouse ? "#1c2831" : "transparent"
                    Rectangle { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: 3; height: 22; radius: 2; color: root.accent; visible: root.page === index }
                    LabelText { anchors.fill: parent; anchors.rightMargin: 18; text: modelData; color: root.page === index ? root.accent : "#9aadb8"; font.weight: root.page === index ? Font.DemiBold : Font.Normal }
                    MouseArea { id: navMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.page = index }
                }
            }
            Item { Layout.fillHeight: true }
            Rectangle {
                Layout.fillWidth: true; height: 92; radius: 12; color: "#18242b"; border.color: "#2b393e"
                ColumnLayout { anchors.fill: parent; anchors.margins: 13
                    LabelText { text: root.d.mode === "demo" ? "وضع المحاكاة" : "وضع MT5"; color: root.d.mode === "demo" ? root.accent : root.green; Layout.fillWidth: true }
                    LabelText { text: root.d.mode === "demo" ? "تجربة بدون أوامر للوسيط" : "راقب الحساب قبل تفعيل الدخول"; font.pixelSize: 11; color: root.muted; Layout.fillWidth: true }
                    LabelText { text: "الإصدار 0.1.1"; font.pixelSize: 11; color: "#607786"; Layout.fillWidth: true }
                }
            }
        }
    }

    ColumnLayout {
        anchors.left: parent.left; anchors.right: sidebar.left; anchors.top: parent.top; anchors.bottom: parent.bottom
        anchors.margins: 28; spacing: 24
        RowLayout {
            Layout.fillWidth: true; spacing: 12
            Action { objectName:"entryToggle"; text: root.d.entry_pending ? "جارٍ تأكيد الطلب…" : root.d.paused ? "تفعيل الدخول" : "إيقاف الدخول الجديد"; primary: root.d.paused; enabled: root.d.engine_connected && !root.d.entry_pending; onClicked: { if (root.d.paused && !root.d.connected) {root.message=root.d.mt5_error || "اربط حساب MT5 من صفحة الاتصالات أولًا";toastTimer.restart();return} if (root.d.paused && root.d.mode === "live") enableDialog.open(); else root.send("pause", {paused:!root.d.paused}) } }
            Rectangle { width: engineLabel.implicitWidth + 28; height: 32; radius: 16; color: root.d.engine_connected ? "#17372d" : "#392928"
                LabelText { id: engineLabel; objectName:"engineStatusLabel"; anchors.centerIn: parent; text: root.d.engine_state === "stopped" ? "● المحرك متوقف" : root.d.engine_state === "stopping" ? "● جارٍ إيقاف المحرك" : root.d.engine_state === "stop_failed" ? "● لم يتأكد إيقاف المحرك" : root.d.engine_connected ? "● المحرك متصل" : "● جارٍ الاتصال بالمحرك"; font.pixelSize: 12; color: root.d.engine_connected ? root.green : "#e7a090" }
            }
            Item { Layout.fillWidth: true }
            ColumnLayout {
                LabelText { text: root.titles[root.page]; font.pixelSize: 29; font.weight: Font.DemiBold; Layout.alignment: Qt.AlignRight }
                LabelText { text: root.d.settings && root.d.settings.channel_name ? root.d.settings.channel_name : "متابعة الإشارات وتنفيذها وإدارة نتائجها"; color: root.muted; font.pixelSize: 12; Layout.alignment: Qt.AlignRight }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#243039" }
        Loader {
            id: pageLoader; objectName:"pageLoader"; Layout.fillWidth: true; Layout.fillHeight: true
            sourceComponent: [dashboardPage, positionsPage, pendingPage, reportsPage, logPage, settingsPage, connectionsPage, simulationPage][root.page]
        }
        RowLayout {
            Layout.fillWidth: true
            LabelText { text: "التنفيذ مستقل عن الواجهة • إغلاق النافذة يترك المحرك شغّالًا"; color: "#647d8d"; font.pixelSize: 11; Layout.fillWidth: true }
            LabelText { text: root.d.mode === "demo" ? "محاكاة محلية" : root.d.connected ? (root.d.account.server || "") + " • الحساب " + (root.d.account.login || "") : "بانتظار MT5"; color: root.accent; font.pixelSize: 11 }
        }
    }

    Component {
        id: dashboardPage
        ScrollView {
            clip: true; contentWidth: availableWidth
            ColumnLayout {
                width: parent.width; spacing: 20
                RowLayout {
                    Layout.fillWidth: true; spacing: 14
                    Metric { label: "الرصيد"; value: root.number(root.d.account.balance); subtitle: root.d.account.currency || "عملة الحساب" }
                    Metric { label: "صافي الربح المسجّل"; value: root.number(root.realized()); valueColor: root.realized() >= 0 ? root.green : "#eb9891"; subtitle: "يشمل الإغلاقات الجزئية والرسوم" }
                    Metric { label: "الربح العائم"; value: root.number(root.floating()); valueColor: root.floating() >= 0 ? root.green : "#eb9891"; subtitle: "للصفقات المفتوحة المتابعة" }
                    Metric { label: "الصفقات المفتوحة"; value: String(root.currentSignals("open").length); subtitle: root.currentSignals("pending").length + " إشارة منتظرة" }
                }
                Card {
                    Layout.fillWidth: true; implicitHeight: 154
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 22; spacing: 24
                        ColumnLayout { Layout.fillWidth: true
                            LabelText { text: root.number(root.d.tick.ask, root.d.digits || 2); font.pixelSize: 36; font.weight: Font.DemiBold; color: root.accent; Layout.fillWidth: true }
                            LabelText { text: "سعر الشراء ASK • " + root.number(root.d.tick.bid, root.d.digits || 2) + " سعر البيع BID"; color: root.muted; Layout.fillWidth: true }
                        }
                        Rectangle { width: 1; Layout.fillHeight: true; color: "#2c3940" }
                        ColumnLayout { Layout.fillWidth: true
                            SectionTitle { objectName:"connectedSymbolLabel"; text: root.d.symbol || "لم يتصل رمز التداول بعد" }
                            LabelText { text: "تلجرام: " + (root.d.telegram_connected ? "● تم الاتصال" : root.d.telegram); color: root.d.telegram_connected ? root.green : root.muted; Layout.fillWidth: true }
                            LabelText { objectName:"dashboardMt5Status"; text: root.d.mode === "demo" ? "● محاكاة محلية" : root.d.connected ? "MT5: ● تم الاتصال • " + (root.d.account.login || "") : "MT5: " + (root.d.mt5_error || "بانتظار الاتصال"); color: root.d.connected ? root.green : "#efa09b"; Layout.fillWidth: true }
                            LabelText { text: !root.d.connected ? "بانتظار اتصال حساب MT5" : root.d.paused ? "الدخول متوقف • الإدارة مستمرة" : !root.d.quote_ready ? "الدخول مفعّل • التنفيذ ينتظر سعرًا صالحًا" : "استقبال وتنفيذ الإشارات مفعّل"; color: root.accent; Layout.fillWidth: true }
                        }
                    }
                }
                LabelText { text:root.d.quote_error || ""; visible:!!root.d.quote_error; color:root.accent; Layout.fillWidth:true; wrapMode:Text.Wrap; elide:Text.ElideNone }
                LabelText { text:root.d.telegram_error || ""; visible:!!root.d.telegram_error; color:"#efa09b"; Layout.fillWidth:true; wrapMode:Text.Wrap; elide:Text.ElideNone }
                Card {
                    Layout.fillWidth: true; implicitHeight: activityColumn.implicitHeight + 44
                    ColumnLayout { id: activityColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 22; spacing: 16
                        RowLayout { Layout.fillWidth: true
                            Action { text: "عرض السجل"; onClicked: root.page = 4 }
                            SectionTitle { text: "آخر النشاط" }
                        }
                        Empty { visible: !(root.d.events || []).length; heading: "جاهز لاستقبال أول إشارة"; detail: "اربط الحساب والقناة أو جرّب مختبر المحاكاة" }
                        Repeater { model: (root.d.events || []).slice(0,5)
                            delegate: RowLayout { required property var modelData; Layout.fillWidth: true
                                LabelText { text: root.timeText(modelData.time); color: "#607c8c"; font.pixelSize: 11; Layout.preferredWidth: 155 }
                                LabelText { text: modelData.message; Layout.fillWidth: true; color: modelData.level === "error" ? "#ed9993" : "#b9c9d2" }
                                Rectangle { width: 6; height: 6; radius: 3; color: modelData.level === "error" ? "#ed9993" : root.green }
                            }
                        }
                    }
                }
                Card { visible: (root.d.unresolved || 0) > 0; Layout.fillWidth: true; implicitHeight: 70
                    LabelText { anchors.fill: parent; anchors.margins: 18; text: "في طلبات غير مؤكدة تحتاج مطابقة مع سجل MT5؛ لن تتكرر تلقائيًا"; color: "#efb990"; wrapMode: Text.Wrap; elide: Text.ElideNone }
                }
            }
        }
    }

    component TradeRow: Card {
        property var trade
        Layout.fillWidth: true; implicitHeight: 130
        RowLayout { anchors.fill: parent; anchors.margins: 18; spacing: 18
            LabelText { text: root.number(parent.parent.trade.state === "closed" ? parent.parent.trade.realized : parent.parent.trade.profit); color: Number(parent.parent.trade.state === "closed" ? parent.parent.trade.realized : parent.parent.trade.profit) >= 0 ? root.green : "#ec9790"; font.pixelSize: 21; Layout.preferredWidth: 95 }
            ColumnLayout { Layout.fillWidth: true
                LabelText { text: "SL " + root.number(parent.parent.parent.trade.sl || parent.parent.parent.trade.signal_stop, root.d.digits || 2); color: root.muted; Layout.fillWidth: true }
                LabelText { text: "TP " + (parent.parent.parent.trade.tp ? root.number(parent.parent.parent.trade.tp, root.d.digits || 2) : "—"); color:root.muted; Layout.fillWidth:true }
                LabelText { text: "الأهداف المنفذة: " + (parent.parent.parent.trade.completed || []).length + (parent.parent.parent.trade.secured ? " • تم التأمين" : ""); color: parent.parent.parent.trade.secured ? root.green : root.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode:Text.Wrap; elide:Text.ElideNone }
            }
            ColumnLayout { Layout.fillWidth: true
                LabelText { text: "الدخول " + root.number(parent.parent.parent.trade.fill || parent.parent.parent.trade.entry, root.d.digits || 2); Layout.fillWidth: true }
                LabelText { text: root.number(parent.parent.parent.trade.volume === undefined ? parent.parent.parent.trade.initial_volume : parent.parent.parent.trade.volume, 2) + " لوت • " + root.stateName(parent.parent.parent.trade.state); color: root.muted; font.pixelSize: 12; Layout.fillWidth: true }
            }
            ColumnLayout { Layout.fillWidth: true
                LabelText { text: parent.parent.parent.trade.channel_name + " • " + (parent.parent.parent.trade.symbol || root.d.symbol || ""); Layout.fillWidth: true }
                LabelText { text: "#" + (parent.parent.parent.trade.ticket || parent.parent.parent.trade.message); color: root.muted; font.pixelSize: 12; Layout.fillWidth: true }
            }
            Rectangle { width: 65; height: 30; radius: 7; color: parent.parent.trade.side === "buy" ? "#193a30" : "#3a272b"
                LabelText { anchors.centerIn: parent; text: parent.parent.parent.trade.side === "buy" ? "شراء" : "بيع"; color: parent.parent.parent.trade.side === "buy" ? root.green : "#e59e98" }
            }
        }
    }
    Component {
        id: positionsPage
        ColumnLayout { spacing: 18
            RowLayout { Layout.fillWidth: true
                LabelText { text: "كل إشارة لها إدارة مستقلة • الإغلاقات الجزئية تُحسب ضمن نفس الصفقة"; color: root.muted; Layout.fillWidth: true }
                ComboBox { id: positionFilter; model:["المفتوحة", "المغلقة", "الكل"]; palette.button: "#1a242c"; palette.buttonText: "#edf1f3"; implicitWidth: 160 }
            }
            ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; contentWidth: availableWidth; clip:true
                ColumnLayout { width: parent.width; spacing: 12
                    Repeater { id: positionsRepeater; model: root.currentSignals(positionFilter.currentIndex === 0 ? "open" : positionFilter.currentIndex === 1 ? "closed" : "").filter(s=>s.ticket)
                        delegate: TradeRow { required property var modelData; trade: modelData }
                    }
                    Empty { visible: positionsRepeater.count === 0; heading: "لا توجد صفقات بهذه الحالة"; detail: "تظهر الصفقة هنا بعد تأكيد التنفيذ" }
                }
            }
        }
    }
    Component {
        id: pendingPage
        ScrollView { clip:true; contentWidth: availableWidth
            ColumnLayout { width: parent.width; spacing:14
                LabelText { text: "سعر التنفيذ يجب أن يدخل الهامش قبل انتهاء مهلة الإشارة"; color: root.muted; Layout.fillWidth:true }
                Repeater { model: root.currentSignals("pending")
                    delegate: Card { required property var modelData; Layout.fillWidth:true; implicitHeight: 155
                        RowLayout { anchors.fill:parent; anchors.margins:20; spacing:20
                            Action { text:"إلغاء"; danger:true; onClicked:root.send("cancel", {id:modelData.id}) }
                            ColumnLayout { Layout.fillWidth:true
                                LabelText { text: modelData.expires ? "تنتهي " + root.timeText(modelData.expires) : "بدون انتهاء مهلة"; color:root.accent; Layout.fillWidth:true }
                                LabelText { text:modelData.last_error || "بانتظار دخول السعر ضمن الهامش"; color:root.muted; Layout.fillWidth:true; wrapMode:Text.Wrap; elide:Text.ElideNone }
                            }
                            ColumnLayout { Layout.fillWidth:true
                                SectionTitle { text:(modelData.side === "buy" ? "شراء" : "بيع") + " من " + root.number(modelData.entry) }
                                LabelText { text:root.number(modelData.entry-modelData.config.entry_margin) + " — " + root.number(modelData.entry+modelData.config.entry_margin); color:root.muted; Layout.fillWidth:true }
                                LabelText { text:modelData.channel_name; color:root.muted; font.pixelSize:12; Layout.fillWidth:true }
                            }
                        }
                    }
                }
                Empty { visible:root.currentSignals("pending").length === 0; heading:"ما في إشارات منتظرة"; detail:"أي إشارة خارج الهامش تظهر هنا حتى التنفيذ أو انتهاء المهلة" }
            }
        }
    }
    Component {
        id: logPage
        ScrollView { clip:true; contentWidth:availableWidth
            ColumnLayout { width:parent.width; spacing:8
                LabelText { text:"الرسائل وطلبات التنفيذ محفوظة محليًا • المعروض آخر 150 حدثًا"; color:root.muted; Layout.fillWidth:true; Layout.bottomMargin:12 }
                Repeater { model:root.d.events || []
                    delegate: Card { required property var modelData; Layout.fillWidth:true; implicitHeight:68
                        RowLayout { anchors.fill:parent; anchors.margins:15; spacing:14
                            LabelText { text:root.timeText(modelData.time); font.pixelSize:11; color:root.muted; Layout.preferredWidth:160 }
                            LabelText { text:modelData.message; color:modelData.level === "error" ? "#ee9992" : modelData.level === "warning" ? root.accent : "#c3d1d9"; Layout.fillWidth:true; wrapMode:Text.Wrap; elide:Text.ElideNone }
                        }
                    }
                }
                Empty { visible:!(root.d.events || []).length; heading:"السجل فارغ" }
            }
        }
    }

    Component {
        id: settingsPage
        ColumnLayout {
            spacing:16
            RowLayout { Layout.fillWidth:true
                Action { objectName:"saveSettingsButton"; text:"حفظ الإعدادات"; primary:true; onClicked:root.saveConfig(false) }
                Action { text:"استعادة المحفوظ"; onClicked:{root.cfg=JSON.parse(JSON.stringify(bridge.settings)); root.dirty=false; pageLoader.active=false; pageLoader.active=true} }
                LabelText { text:root.dirty ? "تغييرات غير محفوظة" : "الإعدادات المحفوظة للإشارات الجديدة"; color:root.dirty ? root.accent : root.muted; Layout.fillWidth:true }
            }
            ScrollView { Layout.fillWidth:true; Layout.fillHeight:true; clip:true; contentWidth:availableWidth
                ColumnLayout { width:parent.width; spacing:18
                    Card { Layout.fillWidth:true; implicitHeight: entrySettings.implicitHeight+40
                        ColumnLayout { id:entrySettings; anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:20; spacing:14
                            SectionTitle { text:"الدخول والانتظار" }
                            RowLayout { Layout.fillWidth:true; spacing:18
                                Numeric { label:"هامش الدخول ± دولار"; settingKey:"entry_margin" }
                                Numeric { label:"مدة الانتظار بالدقائق"; settingKey:"wait_minutes" }
                            }
                            Toggle { label:"إلغاء الإشارة بعد انتهاء مدة الانتظار"; settingKey:"expiry_enabled" }
                        }
                    }
                    Card { Layout.fillWidth:true; implicitHeight: riskSettings.implicitHeight+40
                        ColumnLayout { id:riskSettings; anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:20; spacing:14
                            SectionTitle { text:"حجم الصفقة والستوب" }
                            RowLayout { Layout.fillWidth:true; spacing:18
                                Choice { label:"طريقة تحديد الحجم"; settingKey:"size_mode"; labels:["لوت ثابت", "نسبة مخاطرة من الرصيد"]; values:["fixed","risk"] }
                                Numeric { label:root.cfg.size_mode === "fixed" ? "اللوت الثابت" : "نسبة المخاطرة %"; settingKey:root.cfg.size_mode === "fixed" ? "fixed_lot" : "risk_percent" }
                            }
                            RowLayout { Layout.fillWidth:true; spacing:18
                                Choice { label:"طريقة الستوب"; settingKey:"stop_mode"; labels:["ستوب الإشارة مع بديل ثابت", "مسافة ثابتة دائمًا"]; values:["signal","fixed"] }
                                Numeric { label:"المسافة الثابتة بالدولار"; settingKey:"stop_distance" }
                            }
                        }
                    }
                    Card { Layout.fillWidth:true; implicitHeight: targetSettings.implicitHeight+40
                        ColumnLayout { id:targetSettings; anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:20; spacing:14
                            SectionTitle { text:"الأهداف والإغلاق الجزئي" }
                            Choice { label:"مصدر الأهداف"; settingKey:"targets_mode"; labels:["أهداف يدوية", "أهداف القناة", "بدون أهداف"]; values:["manual","channel","none"] }
                            Toggle { label:"تثبيت آخر هدف TP في MT5"; settingKey:"native_tp_enabled" }
                            LabelText { text:"آخر هدف يظهر في خانة TP لدى الوسيط؛ التطبيق يدير الإغلاق الجزئي للأهداف الأولى والتأمين."; color:root.muted; Layout.fillWidth:true }
                            LabelText { text:"50 بيب = " + root.number(50 * root.cfg.pip_size) + " من سعر الرمز • آخر مرحلة تغلق الكمية المتبقية"; color:root.muted; Layout.fillWidth:true }
                            Numeric { label:"قيمة البيب بوحدة سعر الرمز"; settingKey:"pip_size" }
                            ColumnLayout { visible:root.cfg.targets_mode === "manual"; Layout.fillWidth:true; spacing:10
                                Repeater { model:root.cfg.stages
                                    delegate: RowLayout { required property int index; required property var modelData; Layout.fillWidth:true
                                        Action { text:"حذف"; onClicked:{let s=root.cfg.stages.slice();s.splice(index,1);root.setCfg("stages",s)} }
                                        Input { Layout.fillWidth:true; text:String(modelData.lot); placeholderText:"كمية الإغلاق"; onEditingFinished:{let s=root.cfg.stages.map(x=>Object.assign({},x));s[index].lot=Number(text);root.setCfg("stages",s)} }
                                        Input { Layout.fillWidth:true; text:String(modelData.pips); placeholderText:"الهدف بالبيب"; onEditingFinished:{let s=root.cfg.stages.map(x=>Object.assign({},x));s[index].pips=Number(text);root.setCfg("stages",s)} }
                                        LabelText { text:"المرحلة " + (index+1); Layout.preferredWidth:80 }
                                    }
                                }
                                Action { text:"+ إضافة مرحلة"; Layout.alignment:Qt.AlignRight; onClicked:{let s=root.cfg.stages.slice();s.push({pips:s.length?s[s.length-1].pips+50:50,lot:0.01});root.setCfg("stages",s)} }
                            }
                            ColumnLayout { visible:root.cfg.targets_mode === "channel"; Layout.fillWidth:true
                                LabelText { text:"كميات إغلاق أهداف القناة بالترتيب، مفصولة بفاصلة"; color:root.muted; Layout.fillWidth:true }
                                Input { Layout.fillWidth:true; text:root.cfg.channel_target_lots.join(", "); onTextEdited:root.setCfg("channel_target_lots", text.split(",").map(x=>Number(x.trim()))) }
                            }
                        }
                    }
                    Card { Layout.fillWidth:true; implicitHeight: managementSettings.implicitHeight+40
                        ColumnLayout { id:managementSettings; anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:20; spacing:14
                            SectionTitle { text:"التأمين وإدارة القناة" }
                            Toggle { label:"تأمين المتبقي بعد تحقيق الهدف"; settingKey:"breakeven_enabled" }
                            RowLayout { Layout.fillWidth:true; spacing:18
                                Numeric { label:"التأمين بعد المرحلة رقم"; settingKey:"breakeven_stage" }
                                Numeric { label:"ربح مضمون بالبيب (0 = سعر الدخول)"; settingKey:"breakeven_pips" }
                            }
                            Toggle { label:"متابعة «نحجز ربح ونستمر» من القناة"; settingKey:"channel_management" }
                            Toggle { label:"متابعة تعديلات رسائل الإشارة"; settingKey:"follow_edits" }
                            Choice { label:"الصفقات التي يديرها التطبيق"; settingKey:"management_scope"; labels:["صفقات التطبيق فقط", "مع صفقات يدوية محددة", "كل صفقات الرمز المختار"]; values:["app","selected","all"] }
                            Input { visible:root.cfg.management_scope === "selected"; Layout.fillWidth:true; placeholderText:"أرقام الصفقات اليدوية مفصولة بفاصلة"; text:root.cfg.selected_tickets.join(", "); onTextEdited:root.setCfg("selected_tickets",text.split(",").filter(x=>x.trim()).map(x=>Number(x.trim()))) }
                            Toggle { label:"إدارة الصفقات اليدوية الجديدة والمفتوحة حسب إعدادات الستوب والأهداف والتأمين"; settingKey:"manage_manual_stops" }
                        }
                    }
                    Card { Layout.fillWidth:true; implicitHeight: timezoneSettings.implicitHeight+40
                        ColumnLayout { id:timezoneSettings; anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:20; spacing:14
                            SectionTitle { text:"توقيت التقارير" }
                            RowLayout { Layout.fillWidth:true; spacing:18
                                Choice { label:"التوقيت المعتمد"; settingKey:"report_timezone"; labels:["توقيت الوسيط", "التوقيت المحلي"]; values:["broker","local"] }
                                Numeric { label:"فرق توقيت الوسيط عن UTC بالساعات"; settingKey:"report_utc_offset" }
                            }
                            LabelText { text:"MT5 لا يعرض فرق توقيت السيرفر عبر واجهة Python؛ أدخله من ساعة الوسيط. القيمة الأولية UTC."; color:root.accent; wrapMode:Text.Wrap; elide:Text.ElideNone; Layout.fillWidth:true }
                        }
                    }
                }
            }
        }
    }

    Component {
        id: connectionsPage
        ScrollView { clip:true; contentWidth:availableWidth
            ColumnLayout { width:parent.width; spacing:18
                Card { Layout.fillWidth:true; implicitHeight: mt5Settings.implicitHeight+40
                    ColumnLayout { id:mt5Settings; anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:20; spacing:14
                        SectionTitle { text:"MT5 • اكتشاف الحساب والوسيط" }
                        LabelText { objectName:"mt5StatusLabel"; text:root.d.mode === "demo" ? "● محاكاة محلية • افتح وضع MT5 للاتصال بحسابك" : root.d.connected ? "● تم الاتصال بالحساب " + (root.d.account.login || "") + " • الرمز المتصل: " + (root.d.symbol || "") : "● غير متصل بـMT5"; color:root.d.connected ? root.green : "#efa09b"; Layout.fillWidth:true }
                        LabelText { objectName:"mt5ErrorLabel"; text:root.d.mt5_error || ""; visible:!!root.d.mt5_error; color:"#efa09b"; Layout.fillWidth:true; wrapMode:Text.Wrap; elide:Text.ElideNone }
                        LabelText { text:root.d.quote_error || ""; visible:!!root.d.quote_error; color:root.accent; Layout.fillWidth:true; wrapMode:Text.Wrap; elide:Text.ElideNone }
                        LabelText { text:"التداول الآلي غير مسموح؛ فعّله واسمح بواجهة Python في MT5"; visible:root.d.connected && root.d.mode === "live" && !root.d.account.trade_allowed; color:root.accent; Layout.fillWidth:true; wrapMode:Text.Wrap; elide:Text.ElideNone }
                        RowLayout { Layout.fillWidth:true; spacing:12
                            Action { text:"اكتشاف MT5"; onClicked:root.send("discover", {path:terminalPath.text}) }
                            Action { text:"فتح وضع MT5"; primary:root.d.mode !== "live"; onClicked:modeDialog.open() }
                            Action { text:"وضع المحاكاة"; onClicked:bridge.switchMode("demo") }
                            Item { Layout.fillWidth:true }
                        }
                        ComboBox { Layout.fillWidth:true; model:root.d.terminals || []; visible:(root.d.terminals || []).length > 0; onActivated:terminalPath.text=currentText; palette.button:"#1a242c"; palette.buttonText:"#edf1f3" }
                        Input { id:terminalPath; Layout.fillWidth:true; text:root.cfg.terminal_path; placeholderText:"مسار terminal64.exe • اتركه فارغًا للاكتشاف التلقائي" }
                        Input { objectName:"symbolSearchInput"; Layout.fillWidth:true; text:root.symbolSearch; placeholderText:"ابحث باسم رمز MT5 أو وصفه"; onTextEdited:root.symbolSearch=text }
                        RowLayout { Layout.fillWidth:true; spacing:14
                            Input { id:tradingSymbol; objectName:"tradingSymbol"; Layout.fillWidth:true; text:root.cfg.symbol; placeholderText:"رمز التداول • اختر أي زوج من MT5 أو اكتب اسمه"; onTextEdited:root.setCfg("symbol",text) }
                            ComboBox { id:symbolCombo; objectName:"symbolCombo"; Layout.preferredWidth:180; model:root.filteredSymbols.map(s=>s.name); currentIndex:root.filteredSymbols.findIndex(s=>s.name===root.cfg.symbol); displayText:currentIndex < 0 ? "اختر الرمز" : currentText; visible:root.symbolOptions.length > 0; onActivated:root.setCfg("symbol",currentText); palette.button:"#1a242c"; palette.buttonText:"#edf1f3" }
                            Action { text:"الاتصال بالحساب"; enabled:root.d.mode === "live"; onClicked:{root.setCfg("terminal_path",terminalPath.text);root.setCfg("symbol",tradingSymbol.text);root.saveConfig(false);root.send("connect_mt5", {path:terminalPath.text,symbol:tradingSymbol.text})} }
                        }
                        LabelText { objectName:"symbolHelpLabel"; text:root.symbolOptions.length && !root.filteredSymbols.length ? "لا توجد رموز تطابق البحث" : "تظهر رموز Market Watch أولًا. " + (root.d.symbol ? "الرمز المتصل حاليًا: " + root.d.symbol + ". " : "") + "اختر الرمز المطلوب ثم اضغط الاتصال بالحساب."; color:root.muted; Layout.fillWidth:true; wrapMode:Text.Wrap; elide:Text.ElideNone }
                        LabelText { text:"السبريد الحالي: " + root.number(root.d.spread,root.d.digits || 2) + " • أقل مسافة ستوب للوسيط: " + root.number(root.d.stop_minimum,root.d.digits || 2); visible:!!root.d.quote_ready; color:root.muted; Layout.fillWidth:true }
                        LabelText { text:"الدخول الفعلي يبدأ فقط بعد ضغط «تفعيل الدخول». الحساب يجب أن يدعم Hedging."; color:root.muted; Layout.fillWidth:true; wrapMode:Text.Wrap; elide:Text.ElideNone }
                    }
                }
                Card { Layout.fillWidth:true; implicitHeight: telegramSettings.implicitHeight+40
                    ColumnLayout { id:telegramSettings; anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:20; spacing:14
                        SectionTitle { text:"تلجرام • حساب المشترك" }
                        LabelText { objectName:"telegramStatusLabel"; text:root.d.telegram_busy ? "● جارٍ الاتصال أو التحقق" : root.d.telegram_connected ? "● تم الاتصال بتلجرام" : "● " + root.d.telegram; color:root.d.telegram_connected ? root.green : root.d.telegram_busy ? root.accent : "#efa09b"; Layout.fillWidth:true }
                        LabelText { objectName:"telegramErrorLabel"; text:root.d.telegram_error || ""; visible:!!root.d.telegram_error; color:"#efa09b"; Layout.fillWidth:true; wrapMode:Text.Wrap; elide:Text.ElideNone }
                        RowLayout { Layout.fillWidth:true; spacing:14
                            Input { id:apiHash; Layout.fillWidth:true; placeholderText:"API Hash"; echoMode:TextInput.Password }
                            Input { id:apiId; Layout.fillWidth:true; placeholderText:"API ID" }
                            Action { text:"ربط API"; onClicked:{root.send("telegram_connect",{api_id:Number(apiId.text),api_hash:apiHash.text});apiHash.clear()} }
                        }
                        RowLayout { Layout.fillWidth:true; spacing:14
                            Action { text:"إرسال رمز التحقق"; onClicked:root.send("telegram_code", {phone:phone.text}) }
                            Input { id:phone; Layout.fillWidth:true; placeholderText:"رقم الهاتف مع رمز البلد، مثل +..." }
                        }
                        RowLayout { Layout.fillWidth:true; spacing:14
                            Action { text:"تسجيل الدخول"; primary:true; onClicked:{root.send("telegram_login",{code:loginCode.text,password:password.text});loginCode.clear();password.clear()} }
                            Input { id:password; Layout.fillWidth:true; placeholderText:"كلمة مرور التحقق بخطوتين إذا طُلبت"; echoMode:TextInput.Password }
                            Input { id:loginCode; Layout.fillWidth:true; placeholderText:"رمز التحقق" }
                        }
                        RowLayout { Layout.fillWidth:true
                            Action { text:"my.telegram.org"; onClicked:Qt.openUrlExternally("https://my.telegram.org/apps") }
                            Action { text:"تسجيل خروج تلجرام"; danger:true; onClicked:root.send("telegram_logout") }
                            LabelText { text:"الجلسة محفوظة بتشفير Windows الخاص بالمستخدم"; color:root.muted; Layout.fillWidth:true }
                        }
                    }
                }
                Card { Layout.fillWidth:true; implicitHeight: channelSettings.implicitHeight+40
                    ColumnLayout { id:channelSettings; anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:20; spacing:14
                        SectionTitle { text:"القناة ومصدر الإشارات" }
                        ComboBox { id:channelCombo; objectName:"channelCombo"; Layout.fillWidth:true; model:root.channelOptions; textRole:"name"; currentIndex:Math.max(0,root.channelOptions.findIndex(x=>x.id===root.selectedChannelId)); onActivated:root.selectedChannelId=root.channelOptions[index].id; palette.button:"#1a242c"; palette.buttonText:"#edf1f3" }
                        CheckBox { id:cancelOld; text:"إلغاء الإشارات المنتظرة من القناة السابقة عند التغيير"; checked:true; palette.windowText:"#d0dde5"; palette.highlight:root.green }
                        RowLayout { Layout.fillWidth:true
                            Action { objectName:"chooseChannelButton"; text:"تثبيت القناة المختارة"; primary:true; enabled:root.channelOptions.length>0 && root.d.engine_connected; onClicked:{let ch=root.channelOptions[channelCombo.currentIndex];if(ch){root.selectedChannelId=ch.id;root.dirty=false;root.send("choose_channel",{id:ch.id,name:ch.name,settings:root.cfg,cancel_previous:cancelOld.checked})}} }
                            LabelText { objectName:"savedChannelLabel"; text:bridge.settings.channel_name ? "القناة المثبتة: " + bridge.settings.channel_name : "لم تُحدد قناة بعد"; color:bridge.settings.channel_id ? root.green : root.muted; Layout.fillWidth:true }
                        }
                        LabelText { objectName:"channelListeningLabel"; visible:!!bridge.settings.channel_id; text:root.d.channel_listening ? "مراقبة الرسائل الجديدة مفعّلة • تشمل الرسائل التي ترسلها أنت" : "مراقبة القناة غير جاهزة • " + (root.d.telegram_error || root.d.telegram || "بانتظار الاتصال"); color:root.d.channel_listening ? root.green : root.accent; Layout.fillWidth:true }
                    }
                }
                LabelText { visible:root.d.engine_state === "stop_failed"; text:root.d.stop_error || "تعذر تأكيد إيقاف المحرك؛ أعد طلب الإيقاف"; color:"#ec9790"; wrapMode:Text.Wrap; Layout.fillWidth:true }
                Action { objectName:"startEngineButton"; text:"تشغيل المحرك"; primary:true; visible:root.d.engine_state === "stopped"; Layout.alignment:Qt.AlignRight; onClicked:bridge.startEngine() }
                Action { objectName:"stopEngineButton"; text:"إيقاف المحرك بالكامل"; danger:true; enabled:root.d.engine_state !== "stopped" && root.d.engine_state !== "stopping"; Layout.alignment:Qt.AlignRight; onClicked:stopDialog.open() }
            }
        }
    }

    Component {
        id: simulationPage
        ColumnLayout { spacing:20
            LabelText { text:"جرّب قواعد التنفيذ بدون أي أوامر لحساب الوسيط"; color:root.muted; Layout.fillWidth:true }
            Card { Layout.fillWidth:true; implicitHeight:120
                RowLayout { anchors.fill:parent; anchors.margins:22; spacing:18
                    Action { text:"تحديث السعر"; primary:true; enabled:root.d.mode === "demo"; onClicked:root.send("demo_price", {price:Number(simPrice.text)}) }
                    Input { id:simPrice; text:"4154"; Layout.fillWidth:true }
                    ColumnLayout { Layout.fillWidth:true
                        SectionTitle { text:"سعر الذهب التجريبي BID" }
                        LabelText { text:"السبريد التجريبي 0.20 دولار"; color:root.muted; Layout.fillWidth:true }
                    }
                }
            }
            Card { Layout.fillWidth:true; Layout.fillHeight:true
                ColumnLayout { anchors.fill:parent; anchors.margins:22; spacing:16
                    SectionTitle { text:"رسالة إشارة تجريبية" }
                    TextArea { id:simText; Layout.fillWidth:true; Layout.fillHeight:true; text:"اشتري ذهب الآن من 4\nستوب 3.5"; color:"#e2ecee"; selectionColor:"#517c6e"; horizontalAlignment:Text.AlignRight; font.pixelSize:18; wrapMode:TextEdit.Wrap
                        background:Rectangle { color:"#0d161e"; radius:10; border.color:"#2b3943" }
                    }
                    RowLayout { Layout.fillWidth:true
                        Action { text:"إرسال للمحرك"; primary:true; enabled:root.d.mode === "demo"; onClicked:root.send("demo_signal",{text:simText.text}) }
                        Action { text:"متاحة"; enabled:root.d.mode === "demo"; onClicked:root.send("demo_signal",{text:"متاحة"}) }
                        Action { text:"حجز ربح"; enabled:root.d.mode === "demo"; onClicked:root.send("demo_signal",{text:"نحجز ربح ونستمر"}) }
                        Item { Layout.fillWidth:true }
                    }
                    LabelText { text:"لا توجد بيانات نتائج مصطنعة؛ تظهر نتائج العمليات التي تجربها فقط"; color:root.muted; Layout.fillWidth:true }
                }
            }
        }
    }

    Component {
        id: reportsPage
        ColumnLayout { spacing:18
            LabelText { text:"نتائج الحساب الحالي فقط • " + (root.d.account.login || root.d.account_id || "بانتظار الاتصال"); color:root.muted; Layout.fillWidth:true }
            RowLayout { Layout.fillWidth:true; spacing:10
                Action { text:"تصدير Excel"; enabled:Boolean(root.d.report.start); onClicked:bridge.exportReport("xlsx") }
                Action { text:"PDF"; enabled:Boolean(root.d.report.start); onClicked:bridge.exportReport("pdf") }
                Item { Layout.fillWidth:true }
                Action { text:"هذا الشهر"; onClicked:startDate.text=bridge.dateForPeriod("month") }
                Action { text:"آخر 7 أيام"; onClicked:startDate.text=bridge.dateForPeriod("week") }
                Action { text:"اليوم"; onClicked:startDate.text=bridge.today }
            }
            Card { Layout.fillWidth:true; implicitHeight:128
                ColumnLayout { anchors.fill:parent; anchors.margins:18; spacing:12
                    RowLayout { Layout.fillWidth:true; spacing:12
                        Action { text:root.d.report_busy ? "جارٍ إعداد التقرير" : "عرض النتائج"; primary:true; enabled:!root.d.report_busy; onClicked:root.send("report", {start:startDate.text,end:endDate.text,scope:reportScope.currentIndex===0?"all":reportScope.currentIndex===1?"app":"manual",channel:reportChannel.currentIndex===0?null:(root.d.channels || [])[reportChannel.currentIndex-1].id}) }
                        Input { id:endDate; text:bridge.today; placeholderText:"إلى YYYY-MM-DD"; Layout.fillWidth:true }
                        Input { id:startDate; text:bridge.today; placeholderText:"من YYYY-MM-DD"; Layout.fillWidth:true }
                    }
                    RowLayout { Layout.fillWidth:true; spacing:12
                        ComboBox { id:reportScope; model:["كل الصفقات المتابعة","صفقات التطبيق","الصفقات اليدوية"]; palette.button:"#1a242c"; palette.buttonText:"#edf1f3"; Layout.fillWidth:true }
                        ComboBox { id:reportChannel; model:["كل القنوات"].concat((root.d.channels || []).map(c=>c.name)); palette.button:"#1a242c"; palette.buttonText:"#edf1f3"; Layout.fillWidth:true }
                        LabelText { text:root.d.report.timezone || "التوقيت حسب الإعدادات"; color:root.muted }
                    }
                }
            }
            RowLayout { Layout.fillWidth:true; spacing:14
                Metric { label:"صافي الربح المحقق"; value:root.number(root.d.report.net); valueColor:Number(root.d.report.net || 0)>=0?root.green:"#ec9790"; subtitle:"بعد العمولة والسواب والرسوم" }
                Metric { label:"الصفقات المغلقة"; value:String(root.d.report.closed_count || 0); subtitle:"الإغلاقات الجزئية لا تضاعف العدد" }
                Metric { label:"نسبة النجاح"; value:root.number(root.d.report.win_rate,1)+"%"; subtitle:"للصفقات المغلقة بالكامل فقط" }
            }
            Card { Layout.fillWidth:true; Layout.fillHeight:true
                ColumnLayout { anchors.fill:parent; anchors.margins:22; spacing:14
                    SectionTitle { text:"صافي النتائج حسب اليوم" }
                    ScrollView { Layout.fillWidth:true; Layout.fillHeight:true; contentWidth:availableWidth; clip:true
                        ColumnLayout { width:parent.width; spacing:12
                            Repeater { model:root.d.report.daily || []
                                delegate: RowLayout { required property var modelData; Layout.fillWidth:true; spacing:16
                                    LabelText { text:root.number(modelData.net); color:modelData.net>=0?root.green:"#ec9790"; Layout.preferredWidth:100 }
                                    Rectangle { Layout.fillWidth:true; height:22; color:"#0d171f"; radius:5
                                        Rectangle { anchors.right:parent.right; height:parent.height; width:parent.width*Math.min(1,Math.abs(modelData.net)/Math.max(1,...(root.d.report.daily || []).map(x=>Math.abs(x.net)))); radius:5; color:modelData.net>=0?"#397d66":"#a65f5d" }
                                    }
                                    LabelText { text:modelData.day; color:root.muted; Layout.preferredWidth:110 }
                                }
                            }
                            Empty { visible:!(root.d.report.daily || []).length; heading:"ما في نتائج للفترة المختارة"; detail:"جهّز التقرير بعد تنفيذ وإغلاق الصفقات" }
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id:enableDialog; objectName:"enableEntryDialog"; anchors.centerIn:parent; modal:true; title:"تفعيل التنفيذ على MT5"; width:520
        footer: RowLayout { spacing:12
            Action { text:"إلغاء"; onClicked:enableDialog.reject() }
            Action { text:"تفعيل الدخول"; primary:true; onClicked:enableDialog.accept() }
        }
        onAccepted:root.send("pause",{paused:false})
        Label { width:parent.width; wrapMode:Text.Wrap; text:"سيبدأ تنفيذ إشارات القناة على الحساب " + (root.d.account.login || "") + " لدى " + (root.d.account.server || "") + ".\nالقناة: " + (root.cfg.channel_name || "غير محددة") + "\nالهامش ±" + root.cfg.entry_margin + " دولار، واللوت حسب إعداداتك." }
    }
    Dialog {
        id:modeDialog; anchors.centerIn:parent; modal:true; title:"الاتصال بـMT5"; width:490
        footer: RowLayout { spacing:12
            Action { text:"إلغاء"; onClicked:modeDialog.reject() }
            Action { text:"فتح وضع MT5"; primary:true; onClicked:modeDialog.accept() }
        }
        onAccepted:bridge.switchMode("live")
        Label { width:parent.width; wrapMode:Text.Wrap; text:"سيُفتح محرك MT5 المستقل. ربط الحساب يقرأ بياناته فقط؛ تفعيل الدخول يتم من الزر العلوي.\nبيانات المحاكاة منفصلة عن التداول الفعلي." }
    }
    Dialog {
        id:stopDialog; objectName:"stopEngineDialog"; anchors.centerIn:parent; modal:true; title:"إيقاف المحرك بالكامل"; width:490
        footer: RowLayout { spacing:12
            Action { text:"إلغاء"; onClicked:stopDialog.reject() }
            Action { text:"إيقاف المحرك"; danger:true; onClicked:stopDialog.accept() }
        }
        onAccepted:bridge.stopEngine()
        Label { width:parent.width; wrapMode:Text.Wrap; text:"سيتوقف استقبال الإشارات والإغلاق الجزئي والتأمين. تبقى أوامر الستوب والهدف المسجلة لدى الوسيط فعّالة.\nلإيقاف الدخول فقط، استخدم الزر العلوي." }
    }
    Rectangle {
        visible:root.message.length>0; z:99
        anchors.bottom:parent.bottom; anchors.horizontalCenter:parent.horizontalCenter; anchors.bottomMargin:55
        width:Math.min(800,parent.width-100); height:70; radius:12; color:"#293c36"; border.color:"#517465"
        LabelText { anchors.fill:parent; anchors.margins:16; text:root.message; wrapMode:Text.Wrap; elide:Text.ElideNone }
    }
}
