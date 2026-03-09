import os

def process_tickers():
    base_path = 'config'
    
    # Read existing files
    try:
        with open(os.path.join(base_path, 'ticker.txt'), 'r') as f:
            content = f.read().replace('\n', ',')
            main_tickers = [t.strip().upper() for t in content.split(',') if t.strip()]
    except FileNotFoundError:
        main_tickers = []

    try:
        with open(os.path.join(base_path, 'ticker_arg.txt'), 'r') as f:
            content = f.read().replace('\n', ',')
            arg_tickers = [t.strip().upper() for t in content.split(',') if t.strip()]
    except FileNotFoundError:
        arg_tickers = []

    sec_list = set()
    global_list = set()
    arg_list = set()

    # Process Main Tickers
    for t in main_tickers:
        if 'USD' in t: # Crypto/Forex -> Global
            global_list.add(t)
        elif '.' in t: # Suffix -> Global (likely)
             # Handle exceptions like BRK.B if format differs, but yfinance usually BRK-B
             # If it has a suffix like .SA, .DE, .T, .HK -> Global
             parts = t.split('.')
             if len(parts) > 1 and len(parts[1]) >= 1:
                 global_list.add(t)
             else:
                 sec_list.add(t)
        else:
            sec_list.add(t)

    # Process Arg Tickers
    for t in arg_tickers:
        if t.endswith('.BA'):
            arg_list.add(t)
        else:
            # ADRs or US listed Arg stocks -> SEC
            sec_list.add(t)

    # Fix specific cases if any (e.g. BRK-B goes to SEC)
    # The heuristic above puts BRK-B in SEC because no '.'
    
    # Write files
    def write_list(filename, tickers):
        with open(os.path.join(base_path, filename), 'w') as f:
            f.write(','.join(sorted(list(tickers))))

    write_list('ticker_sec.txt', sec_list)
    write_list('ticker_global.txt', global_list)
    write_list('ticker_arg.txt', arg_list)
    
    print(f"Created ticker_sec.txt with {len(sec_list)} tickers.")
    print(f"Created ticker_global.txt with {len(global_list)} tickers.")
    print(f"Created ticker_arg.txt with {len(arg_list)} tickers.")

if __name__ == "__main__":
    process_tickers()
