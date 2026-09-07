import 'dart:async';
import 'dart:convert';

import 'package:fl_chart/fl_chart.dart';
import 'package:http/http.dart' as http;
import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

const stoicBlue = Color(0xFF0077FF);
const stoicGold = Color(0xFFFFD700);
const stoicDark = Color(0xFF121212);
const stoicPanel = Color(0xFF1B1D22);
const stoicMuted = Color(0xFF9CA3AF);
const marketPulseUrl = String.fromEnvironment('M_STOIC_API_URL');

class MarketPulseService {
  Future<MarketPulse> fetch() async {
    if (marketPulseUrl.isEmpty) return MarketPulse.fallback();
    final response = await http.get(Uri.parse('$marketPulseUrl/pulse')).timeout(const Duration(seconds: 5));
    if (response.statusCode != 200) throw Exception('Market pulse unavailable');
    return MarketPulse.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }
}

class MarketPulse {
  final String symbol;
  final double price;
  final double change;
  final String status;

  const MarketPulse({required this.symbol, required this.price, required this.change, required this.status});

  factory MarketPulse.fromJson(Map<String, dynamic> json) => MarketPulse(
        symbol: json['symbol'] as String? ?? 'XAUUSD',
        price: (json['price'] as num?)?.toDouble() ?? 0,
        change: (json['change'] as num?)?.toDouble() ?? 0,
        status: json['status'] as String? ?? 'Live',
      );

  factory MarketPulse.fallback() => const MarketPulse(symbol: 'XAUUSD', price: 2338.60, change: 0.84, status: 'Paper feed');
}

void main() {
  runApp(const MStoicApp());
}

class MStoicApp extends StatelessWidget {
  const MStoicApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'M-STOIC',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: stoicDark,
        colorScheme: ColorScheme.fromSeed(
          seedColor: stoicBlue,
          brightness: Brightness.dark,
          primary: stoicBlue,
          secondary: stoicGold,
          surface: stoicPanel,
        ),
        fontFamily: 'sans-serif',
        useMaterial3: true,
      ),
      home: const AppShell(),
    );
  }
}

class AppShell extends StatefulWidget {
  const AppShell({super.key});

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  int _index = 0;
  bool _signedIn = false;

  @override
  Widget build(BuildContext context) {
    if (!_signedIn) {
      return WelcomeScreen(onContinue: () => setState(() => _signedIn = true));
    }

    final screens = [
      const DashboardScreen(),
      const TradingScreen(),
      const ProfileScreen(),
    ];

    return Scaffold(
      body: SafeArea(child: screens[_index]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (value) => setState(() => _index = value),
        backgroundColor: stoicPanel,
        indicatorColor: stoicBlue.withValues(alpha: 0.22),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.grid_view_outlined), selectedIcon: Icon(Icons.grid_view), label: 'Dashboard'),
          NavigationDestination(icon: Icon(Icons.show_chart_outlined), selectedIcon: Icon(Icons.show_chart), label: 'Trade'),
          NavigationDestination(icon: Icon(Icons.shield_outlined), selectedIcon: Icon(Icons.shield), label: 'Safety'),
        ],
      ),
    );
  }
}

class WelcomeScreen extends StatelessWidget {
  final VoidCallback onContinue;

  const WelcomeScreen({required this.onContinue, super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            colors: [Color(0xFF080B12), Color(0xFF121212), Color(0xFF071C36)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
        ),
        child: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 460),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Image.asset('assets/m-stoic-logo.png', height: 220),
                    const SizedBox(height: 12),
                    const Text('The disciplined trading desk.', textAlign: TextAlign.center, style: TextStyle(color: stoicMuted, fontSize: 16)),
                    const SizedBox(height: 36),
                    FilledButton(onPressed: onContinue, child: const Text('Enter paper desk')),
                    const SizedBox(height: 12),
                    OutlinedButton(onPressed: onContinue, child: const Text('Sign in')),
                    const SizedBox(height: 20),
                    const SafetyBanner(),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  final _pulseService = MarketPulseService();
  Timer? _timer;
  MarketPulse _pulse = MarketPulse.fallback();
  int _range = 0;

  @override
  void initState() {
    super.initState();
    _refreshPulse();
    _timer = Timer.periodic(const Duration(seconds: 15), (_) => _refreshPulse());
  }

  Future<void> _refreshPulse() async {
    try {
      final pulse = await _pulseService.fetch();
      if (mounted) setState(() => _pulse = pulse);
    } catch (_) {
      if (mounted) setState(() => _pulse = MarketPulse.fallback());
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AppPage(
      title: "Today's Performance",
      subtitle: 'Paper account • ${_pulse.status}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SafetyBanner(),
          const SizedBox(height: 16),
          const ResponsiveGrid(children: [
            MetricCard(label: 'Daily profit', value: '+75.25%', detail: 'Target progress', accent: stoicGold),
            MetricCard(label: 'Trades', value: '24', detail: 'Today', accent: stoicBlue),
            MetricCard(label: 'Win rate', value: '83%', detail: 'Last 20 trades', accent: stoicGold),
            MetricCard(label: 'Profit', value: '3.20R', detail: 'Risk adjusted', accent: stoicBlue),
          ]),
          const SizedBox(height: 16),
          Panel(
            title: 'Performance',
            trailing: ChartRangeTabs(selected: _range, onChanged: (value) => setState(() => _range = value)),
            child: SizedBox(height: 190, child: PerformanceChart(range: _range)),
          ),
          const SizedBox(height: 16),
          Panel(title: 'Market pulse • ${_pulse.symbol}', child: Row(children: [
            Text(_pulse.price.toStringAsFixed(2), style: const TextStyle(color: stoicGold, fontWeight: FontWeight.w800, fontSize: 24)),
            const SizedBox(width: 12),
            Text('${_pulse.change >= 0 ? '+' : ''}${_pulse.change.toStringAsFixed(2)}%', style: TextStyle(color: _pulse.change >= 0 ? Colors.green : Colors.red, fontWeight: FontWeight.w700)),
          ])),
          const SizedBox(height: 16),
          const Panel(
            title: 'Discipline status',
            child: Column(children: [
              StatusRow(label: 'News blackout', value: 'Clear', color: Colors.green),
              StatusRow(label: 'Correlation guard', value: 'Monitoring', color: stoicBlue),
              StatusRow(label: 'Profit lock', value: 'Armed at 50%', color: stoicGold),
            ]),
          ),
        ],
      ),
    );
  }
}

class TradingScreen extends StatefulWidget {
  const TradingScreen({super.key});

  @override
  State<TradingScreen> createState() => _TradingScreenState();
}

class _TradingScreenState extends State<TradingScreen> {
  String _symbol = 'XAUUSD';
  bool _paused = false;

  @override
  Widget build(BuildContext context) {
    return AppPage(
      title: 'Scalping Mode Active',
      subtitle: 'Paper execution only',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [
            Expanded(child: DropdownButtonFormField<String>(value: _symbol, decoration: const InputDecoration(labelText: 'Instrument'), items: const ['XAUUSD', 'USDZAR', 'EURUSD'].map((value) => DropdownMenuItem(value: value, child: Text(value))).toList(), onChanged: (value) => setState(() => _symbol = value ?? _symbol))),
            const SizedBox(width: 12),
            IconButton.filled(onPressed: () => setState(() => _paused = !_paused), icon: Icon(_paused ? Icons.play_arrow : Icons.pause), tooltip: _paused ? 'Resume trading' : 'Pause trading'),
          ]),
          const SizedBox(height: 16),
          Panel(title: '$_symbol • SELL signal', child: SizedBox(height: 220, child: CandleChart())),
          const SizedBox(height: 16),
          const Panel(title: 'Open trades', child: Column(children: [
            TradeRow(side: 'BUY', symbol: 'EUR/USD', profit: '+20.5 pips', volume: '1.25'),
            TradeRow(side: 'SELL', symbol: 'GBP/USD', profit: '+15.8 pips', volume: '0.50'),
            TradeRow(side: 'BUY', symbol: 'USD/JPY', profit: '+32.1 pips', volume: '1.00'),
          ])),
          const SizedBox(height: 16),
          const ResponsiveGrid(children: [
            MetricCard(label: 'Conviction', value: '82/100', detail: 'High confidence', accent: stoicGold),
            MetricCard(label: 'Spread', value: '12 pts', detail: 'Within limit', accent: Colors.green),
            MetricCard(label: 'Risk size', value: '0.35x', detail: 'Hedge adjusted', accent: stoicBlue),
          ]),
          const SizedBox(height: 16),
          Panel(title: 'Trade controls', child: Row(children: [
            Expanded(child: FilledButton.icon(onPressed: _paused ? null : () {}, icon: const Icon(Icons.play_arrow), label: const Text('Open paper trade'))),
            const SizedBox(width: 10),
            Expanded(child: OutlinedButton.icon(onPressed: () {}, icon: const Icon(Icons.lock_outline), label: const Text('Profit lock'))),
          ])),
        ],
      ),
    );
  }
}

class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return AppPage(
      title: 'Safety centre',
      subtitle: 'Capital protection is always visible',
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: const [
        SafetyBanner(),
        SizedBox(height: 16),
        Panel(title: 'Active safeguards', child: Column(children: [
          StatusRow(label: 'Execution mode', value: 'PAPER', color: stoicBlue),
          StatusRow(label: 'Consecutive-loss pause', value: '3 losses', color: Colors.orange),
          StatusRow(label: 'Volatility control', value: 'Enabled', color: Colors.green),
          StatusRow(label: 'NFP / major news filter', value: 'Enabled', color: Colors.green),
        ])),
        SizedBox(height: 16),
        Panel(title: 'Account', child: Column(children: [
          StatusRow(label: 'Profile', value: 'Balanced', color: stoicGold),
          StatusRow(label: 'Preferred strategy', value: 'Breakout', color: stoicBlue),
          StatusRow(label: 'Risk per trade', value: '1.0%', color: stoicGold),
        ])),
      ]),
    );
  }
}

class AppPage extends StatelessWidget {
  final String title;
  final String subtitle;
  final Widget child;

  const AppPage({required this.title, required this.subtitle, required this.child, super.key});

  @override
  Widget build(BuildContext context) {
    return CustomScrollView(slivers: [
      SliverAppBar(
        pinned: true,
        backgroundColor: stoicDark,
        title: Row(children: [
          Image.asset('assets/m-stoic-logo.png', width: 42, height: 42),
          const SizedBox(width: 10),
          Text(title),
        ]),
      ),
      SliverPadding(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), sliver: SliverList(delegate: SliverChildListDelegate([
        Text(subtitle, style: const TextStyle(color: stoicMuted)),
        const SizedBox(height: 16),
        child,
      ]))),
    ]);
  }
}

class Panel extends StatelessWidget {
  final String title;
  final Widget child;
  final Widget? trailing;

  const Panel({required this.title, required this.child, this.trailing, super.key});

  @override
  Widget build(BuildContext context) {
    return Card(
      color: stoicPanel,
      margin: EdgeInsets.zero,
      child: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [Expanded(child: Text(title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16))), if (trailing != null) trailing!]),
        const SizedBox(height: 14),
        child,
      ])),
    );
  }
}

class MetricCard extends StatelessWidget {
  final String label;
  final String value;
  final String detail;
  final Color accent;

  const MetricCard({required this.label, required this.value, required this.detail, required this.accent, super.key});

  @override
  Widget build(BuildContext context) {
    return Card(color: stoicPanel, margin: EdgeInsets.zero, child: Padding(padding: const EdgeInsets.all(14), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: const TextStyle(color: stoicMuted, fontSize: 12)),
      const SizedBox(height: 8),
      Text(value, style: TextStyle(color: accent, fontSize: 23, fontWeight: FontWeight.w800)),
      const SizedBox(height: 4),
      Text(detail, style: const TextStyle(color: stoicMuted, fontSize: 12)),
    ])));
  }
}

class ResponsiveGrid extends StatelessWidget {
  final List<Widget> children;

  const ResponsiveGrid({required this.children, super.key});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, constraints) {
      final columns = constraints.maxWidth >= 720 ? 4 : constraints.maxWidth >= 430 ? 2 : 1;
      return GridView.count(crossAxisCount: columns, shrinkWrap: true, physics: const NeverScrollableScrollPhysics(), mainAxisSpacing: 10, crossAxisSpacing: 10, childAspectRatio: columns == 1 ? 3.3 : 1.45, children: children);
    });
  }
}

class SafetyBanner extends StatelessWidget {
  const SafetyBanner({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: stoicBlue.withValues(alpha: 0.14), borderRadius: BorderRadius.circular(14), border: Border.all(color: stoicBlue.withValues(alpha: 0.5))), child: const Row(children: [
      Icon(Icons.shield_outlined, color: stoicGold),
      SizedBox(width: 10),
      Expanded(child: Text('PAPER MODE • Live orders disabled. Risk controls are active.', style: TextStyle(fontWeight: FontWeight.w600))),
    ]));
  }
}

class StatusRow extends StatelessWidget {
  final String label;
  final String value;
  final Color color;

  const StatusRow({required this.label, required this.value, required this.color, super.key});

  @override
  Widget build(BuildContext context) {
    return Padding(padding: const EdgeInsets.symmetric(vertical: 8), child: Row(children: [Expanded(child: Text(label, style: const TextStyle(color: stoicMuted))), Text(value, style: TextStyle(color: color, fontWeight: FontWeight.w700))]));
  }
}

class TradeRow extends StatelessWidget {
  final String side;
  final String symbol;
  final String profit;
  final String volume;

  const TradeRow({required this.side, required this.symbol, required this.profit, required this.volume, super.key});

  @override
  Widget build(BuildContext context) {
    final isBuy = side == 'BUY';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(children: [
        Icon(isBuy ? Icons.check_box : Icons.check_box_outline_blank, color: isBuy ? stoicBlue : stoicGold),
        const SizedBox(width: 10),
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('$side $volume $symbol', style: const TextStyle(fontWeight: FontWeight.w700)),
          const Text('Scalp', style: TextStyle(color: stoicMuted, fontSize: 12)),
        ])),
        Text(profit, style: const TextStyle(color: Colors.green, fontWeight: FontWeight.w700)),
        const SizedBox(width: 8),
        const Icon(Icons.chevron_right, color: stoicMuted),
      ]),
    );
  }
}

class ChartRangeTabs extends StatelessWidget {
  final int selected;
  final ValueChanged<int> onChanged;

  const ChartRangeTabs({required this.selected, required this.onChanged, super.key});

  @override
  Widget build(BuildContext context) {
    return ToggleButtons(
      isSelected: [selected == 0, selected == 1, selected == 2],
      onPressed: onChanged,
      borderRadius: BorderRadius.circular(8),
      selectedColor: stoicDark,
      fillColor: stoicGold,
      color: stoicMuted,
      constraints: const BoxConstraints(minWidth: 42, minHeight: 30),
      children: const [Text('1D', style: TextStyle(fontSize: 11)), Text('1W', style: TextStyle(fontSize: 11)), Text('1M', style: TextStyle(fontSize: 11))],
    );
  }
}

class PerformanceChart extends StatelessWidget {
  final int range;

  PerformanceChart({required this.range, super.key});

  List<FlSpot> get spots => [
        const FlSpot(0, 28),
        FlSpot(1, 31 + range * 2),
        FlSpot(2, 29 + range * 4),
        FlSpot(3, 38 + range * 3),
        FlSpot(4, 36 + range * 5),
        FlSpot(5, 48 + range * 4),
        FlSpot(6, 53 + range * 7),
      ];

  @override
  Widget build(BuildContext context) {
    return LineChart(LineChartData(
      minX: 0, maxX: 6, minY: 20, maxY: 60,
      gridData: const FlGridData(show: false),
      titlesData: const FlTitlesData(show: false),
      borderData: FlBorderData(show: false),
      lineBarsData: [LineChartBarData(spots: spots, isCurved: true, color: stoicGold, barWidth: 3, dotData: const FlDotData(show: false), belowBarData: BarAreaData(show: true, color: stoicGold.withValues(alpha: 0.12)))],
    ));
  }
}

class CandleChart extends StatelessWidget {
  const CandleChart({super.key});

  @override
  Widget build(BuildContext context) {
    return LineChart(LineChartData(
      gridData: const FlGridData(show: false),
      titlesData: const FlTitlesData(show: false),
      borderData: FlBorderData(show: false),
      lineBarsData: [LineChartBarData(spots: const [FlSpot(0, 44), FlSpot(1, 41), FlSpot(2, 46), FlSpot(3, 43), FlSpot(4, 39), FlSpot(5, 35), FlSpot(6, 37), FlSpot(7, 32)], color: stoicBlue, barWidth: 3, isCurved: true, dotData: const FlDotData(show: false))],
    ));
  }
}
