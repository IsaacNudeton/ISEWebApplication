import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from supabase import create_client, Client
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode
import csv
import io

# Supabase setup
SUPABASE_URL = "https://gvkjhrbdfkuddkuzrwnp.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd2a2pocmJkZmt1ZGRrdXpyd25wIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTUwMjM3MDUsImV4cCI6MjA3MDU5OTcwNX0.7-EIxaKy58QBFC6iS8_xBq5fB-kG-VXadKzRg5DtbEw"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Custom CSS for mimicking original styles
st.markdown("""
<style>
.ag-row { height: 30px !important; }
.ag-row.even { background-color: lightgray; }
.ag-row.odd { background-color: white; }
.ag-row.complete { background-color: #b6fcb6 !important; }
.ag-row.almost { background-color: #fffcb6 !important; }
.ag-row.shutdown { background-color: #fcb6b6 !important; }
.ag-cell.bim-bold { font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# Helper functions from original, adapted for DB
def parse_custom_date(date_str):
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, '%d-%m-%y_%H:%M:%S.%f')
    except ValueError:
        try:
            dt = datetime.strptime(date_str, '%d-%m-%y_%H:%M:%S')
        except ValueError:
            return None
    return dt

def get_total_run_hours(board_id):
    shutdowns = supabase.table('shutdowns').select('*').eq('board_id', board_id).execute().data
    now = datetime.now()
    total = 0.0
    for shutdown in shutdowns:
        start_dt = parse_custom_date(shutdown['start'])
        end_dt = parse_custom_date(shutdown['end']) or now
        if start_dt and end_dt and start_dt < end_dt:
            total += (end_dt - start_dt).total_seconds() / 3600.0
    return total

def get_progress(board_id, required_duration):
    operational = get_total_run_hours(board_id)
    progress = (operational / required_duration) * 100 if required_duration > 0 else 0
    return progress

def get_remaining_and_finish(board_id, required_duration, is_running_flag):
    operational = get_total_run_hours(board_id)
    remaining_hours = max(0, required_duration - operational)
    if remaining_hours > 0 and is_running_flag:
        finish_time = datetime.now() + timedelta(hours=remaining_hours)
        finish_str = finish_time.strftime('%m/%d/%Y %H:%M')
    elif remaining_hours <= 0:
        finish_str = 'Done'
    else:
        finish_str = ''
    return remaining_hours, finish_str

def is_running(board_id):
    shutdowns = supabase.table('shutdowns').select('*').eq('board_id', board_id).execute().data
    for shutdown in shutdowns:
        if shutdown.get('start') and not shutdown.get('end'):
            return True
    return False

def sort_shutdowns(shutdowns):
    shutdowns.sort(key=lambda s: parse_custom_date(s['start']) or datetime.min)

def update_shutdown_status(board_id, required_duration):
    shutdowns = supabase.table('shutdowns').select('*').eq('board_id', board_id).execute().data
    if not shutdowns:
        return
    sort_shutdowns(shutdowns)
    last = shutdowns[-1]
    if not last.get('end'):
        return
    total = get_total_run_hours(board_id)
    if total >= required_duration:
        supabase.table('shutdowns').update({'cause': 'COMPLETE'}).eq('id', last['id']).execute()
    else:
        cause = last.get('cause', '')
        if '<' not in cause and '>' not in cause:
            supabase.table('shutdowns').update({'cause': ''}).eq('id', last['id']).execute()

# Main app
st.title("Board Shutdown Tracker")

system_names = ["Sonoma 3", "Sonoma 5", "Sonoma 6", "Sonoma 7", "Sonoma 8", "Sonoma 9", "Sonoma 10"]
tabs = st.tabs(system_names)

for i, system_name in enumerate(system_names):
    with tabs[i]:
        # Get or create system
        system = supabase.table("systems").select("id").eq("name", system_name).execute().data
        if not system:
            supabase.table("systems").insert({"name": system_name}).execute()
            system = supabase.table("systems").select("id").eq("name", system_name).execute().data
        system_id = system[0]['id']

        # LOT management
        lots = supabase.table("lots").select("*").eq("system_id", system_id).order("start_time", desc=True).execute().data
        lot_options = {lot['id']: f"{lot['lot_number']} ({datetime.fromisoformat(lot['start_time']).strftime('%m/%d/%Y %H:%M')})" for lot in lots}
        selected_lot_id = st.selectbox("Select LOT", options=list(lot_options.keys()), format_func=lambda k: lot_options.get(k, "No LOT"), key=f"lot_select_{system_name}")
        lot_info = next((lot for lot in lots if lot['id'] == selected_lot_id), None) if selected_lot_id else None

        col1, col2, col3 = st.columns(3)
        with col1:
            with st.form(f"Add LOT_{system_name}"):  # Unique key per tab
                lot_num = st.text_input("LOT #")
                dur = st.number_input("Required Duration (hours)", min_value=0.0)
                start_str = st.text_input("Start Time (mm/dd/yy hh:mm, optional)")
                if st.form_submit_button("Add LOT"):
                    try:
                        start_time = datetime.strptime(start_str, '%m/%d/%y %H:%M') if start_str else datetime.now()
                        supabase.table("lots").insert({
                            "system_id": system_id,
                            "lot_number": lot_num,
                            "start_time": start_time.isoformat(),
                            "required_duration": dur
                        }).execute()
                        st.success("LOT added")
                        st.rerun()
                    except ValueError:
                        st.error("Invalid duration or start time format.")

        with col2:
            if selected_lot_id and st.button("Delete LOT", key=f"delete_lot_{system_name}"):
                # Delete associated boards and shutdowns
                boards = supabase.table("boards").select("id").eq("lot_id", selected_lot_id).execute().data
                board_ids = [b['id'] for b in boards]
                if board_ids:
                    supabase.table("shutdowns").delete().in_("board_id", board_ids).execute()
                supabase.table("boards").delete().eq("lot_id", selected_lot_id).execute()
                supabase.table("lots").delete().eq("id", selected_lot_id).execute()
                st.success("LOT deleted")
                st.rerun()

        with col3:
            if st.button("Refresh", key=f"refresh_{system_name}"):
                st.rerun()

        if lot_info:
            st.subheader(f"Current LOT: {lot_info['lot_number']} | Required: {lot_info['required_duration']:.2f} hours | Started: {datetime.fromisoformat(lot_info['start_time']).strftime('%m/%d/%Y %H:%M')}")

            st.markdown("**Instructions:** Select a row to view/edit shutdowns in the expander below.")

            # Boards table using AgGrid
            boards = supabase.table("boards").select("*").eq("lot_id", selected_lot_id).order("bim").execute().data
            if boards:
                df = pd.DataFrame(boards)
                df['lot'] = lot_info['lot_number']
                df['total_hours_ran'] = df['id'].apply(get_total_run_hours)
                df['progress'] = df['id'].apply(lambda id: get_progress(id, lot_info['required_duration']))
                df['running'] = df['id'].apply(is_running)
                df['datalogs'] = df['id'].apply(lambda id: supabase.table('shutdowns').select('*', count='exact').eq('board_id', id).not_.is_('datalog', 'null').execute().count)
                df['download_date'] = df.apply(lambda row: get_remaining_and_finish(row['id'], lot_info['required_duration'], row['running'])[1], axis=1)

                bim_count = len(boards)
                total_datalogs = sum(df['datalogs'])
                st.markdown(f"**BIMs:** {bim_count} **Datalogs:** {total_datalogs}")

                # Row class rules with JS expressions
                rowClassRules = {
                    'even': "params.node.rowIndex % 2 === 0",
                    'odd': "params.node.rowIndex % 2 !== 0",
                    'complete': f"params.data.total_hours_ran >= {lot_info['required_duration']}",
                    'almost': f"params.data.total_hours_ran >= {lot_info['required_duration'] * 0.95}",
                    'shutdown': "params.data.running == false"
                }

                gb = GridOptionsBuilder.from_dataframe(df[['ip', 'bim', 'lot', 'dut_sn', 'total_hours_ran', 'progress', 'running', 'datalogs', 'download_date']])
                gb.configure_default_column(rowHeight=30)
                gb.configure_column('bim', cellStyle={'fontWeight': 'bold'})
                gb.configure_column('ip', width=80, headerClass='center', cellStyle={'textAlign': 'center'})
                gb.configure_column('bim', width=80, headerClass='center', cellStyle={'textAlign': 'center'})
                gb.configure_column('lot', width=80, headerClass='center', cellStyle={'textAlign': 'center'})
                gb.configure_column('dut_sn', width=220, cellStyle={'textAlign': 'left'})
                gb.configure_column('total_hours_ran', width=100, headerClass='center', cellStyle={'textAlign': 'center'})
                gb.configure_column('progress', width=200, type=["numericColumn", "sparklineColumn"], cellRendererParams={'sparklineOptions': {'type': 'bar', 'fill': '#00ff9f', 'stroke': '#00ff9f'}})
                gb.configure_column('running', width=80, headerClass='center', cellStyle={'textAlign': 'center'}, cellRenderer="function(params) { return params.value ? '✓' : '' }")
                gb.configure_column('datalogs', width=80, headerClass='center', cellStyle={'textAlign': 'center'})
                gb.configure_column('download_date', width=150, headerClass='center', cellStyle={'textAlign': 'center'})
                gb.configure_selection('single')
                gb.configure_grid_options(rowClassRules=rowClassRules)
                grid_response = AgGrid(
                    df,
                    gridOptions=gb.build(),
                    update_mode=GridUpdateMode.SELECTION_CHANGED,
                    data_return_mode=DataReturnMode.AS_INPUT,
                    height=400,
                    fit_columns_on_grid_load=True
                )

                selected_rows = grid_response['selected_rows']
                selected_board_label = "None"
                if selected_rows:
                    selected_board = pd.DataFrame(selected_rows).iloc[0]
                    selected_board_label = f"BIM {selected_board['bim']}"
                    board_id = selected_board['id']

                    st.write(f"Selected Board: {selected_board_label}")

                    # Shutdown popup as expander
                    with st.expander(f"Shutdowns for {selected_board_label}"):
                        shutdowns = supabase.table('shutdowns').select('*').eq('board_id', board_id).execute().data
                        sort_shutdowns(shutdowns)
                        for j, shutdown in enumerate(shutdowns):
                            col_s1, col_s2, col_s3, col_s4 = st.columns(4)
                            with col_s1:
                                start = st.text_input(f"Start {j+1}", shutdown['start'])
                            with col_s2:
                                end = st.text_input(f"End {j+1}", shutdown['end'])
                            with col_s3:
                                cause = st.text_input(f"Cause {j+1}", shutdown['cause'])
                            with col_s4:
                                if st.button("Save", key=f"save_shut_{j}_{board_id}"):
                                    supabase.table('shutdowns').update({'start': start, 'end': end, 'cause': cause}).eq('id', shutdown['id']).execute()
                                    update_shutdown_status(board_id, lot_info['required_duration'])
                                    st.rerun()
                                if st.button("Remove", key=f"remove_shut_{j}_{board_id}"):
                                    supabase.table('shutdowns').delete().eq('id', shutdown['id']).execute()
                                    st.rerun()
                            if shutdown.get('datalog'):
                                st.write(f"Datalog: {shutdown['datalog']}")

                        # Add new shutdown
                        st.subheader("Add New Shutdown")
                        new_start = st.text_input("Start", datetime.now().strftime('%d-%m-%y_%H:%M:%S'))
                        new_end = st.text_input("End", "")
                        new_cause = st.text_input("Cause", "")
                        if st.button("Add Shutdown", key=f"add_shutdown_{system_name}"):
                            supabase.table('shutdowns').insert({
                                'board_id': board_id,
                                'start': new_start,
                                'end': new_end,
                                'cause': new_cause
                            }).execute()
                            update_shutdown_status(board_id, lot_info['required_duration'])
                            st.rerun()

            else:
                st.info("No boards yet.")

            # Controls (import, export, etc.)
            st.subheader("Actions")
            col_a1, col_a2, col_a3, col_a4 = st.columns(4)
            with col_a1:
                with st.expander("Import Boards List"):
                    text = st.text_area("Paste one DUT SN per line, or IP BIM DUT_SN (space/tab separated)")
                    if st.button("Load Boards", key=f"load_boards_{system_name}"):
                        lines = text.strip().split('\n')
                        new_boards = []
                        auto_bim = max([b['bim'] for b in boards] if boards else [0]) + 1
                        auto_ip = len(boards) + 1
                        for line in lines:
                            if line.strip():
                                parts = line.split()
                                if len(parts) == 1:
                                    dut_sn = parts[0]
                                    ip = f"IP{auto_ip:02d}"
                                    bim = auto_bim
                                    auto_bim += 1
                                    auto_ip += 1
                                    new_boards.append({'lot_id': selected_lot_id, 'ip': ip, 'bim': bim, 'dut_sn': dut_sn})
                                else:
                                    ip = parts[0]
                                    bim = int(parts[1])
                                    dut_sn = ' '.join(parts[2:]) if len(parts) > 2 else ""
                                    new_boards.append({'lot_id': selected_lot_id, 'ip': ip, 'bim': bim, 'dut_sn': dut_sn})
                        if new_boards:
                            supabase.table("boards").insert(new_boards).execute()
                            st.success(f"Loaded {len(new_boards)} boards")
                            st.rerun()

            with col_a2:
                with st.expander("Import DUT SNs"):
                    text = st.text_area("Paste one DUT SN per line")
                    if st.button("Load DUT SNs", key=f"load_dut_{system_name}"):
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        existing_boards = sorted(boards, key=lambda b: b['bim'])
                        updates = []
                        for idx, dut_sn in enumerate(lines):
                            if idx < len(existing_boards):
                                updates.append({'id': existing_boards[idx]['id'], 'dut_sn': dut_sn})
                            else:
                                # Add new
                                bim = len(existing_boards) + idx + 1
                                ip = f"IP{bim:02d}"
                                supabase.table("boards").insert({'lot_id': selected_lot_id, 'ip': ip, 'bim': bim, 'dut_sn': dut_sn}).execute()
                        if updates:
                            for update in updates:
                                supabase.table("boards").update({'dut_sn': update['dut_sn']}).eq('id', update['id']).execute()
                        st.success(f"Updated/added {len(lines)} DUT SNs")
                        st.rerun()

            with col_a3:
                with st.expander("Upload Datalogs"):
                    uploaded_files = st.file_uploader("Choose CSV files", accept_multiple_files=True, type="csv", key=f"uploader_{system_name}")
                    if st.button("Process Uploaded Files", key=f"process_files_{system_name}"):
                        for uploaded_file in uploaded_files:
                            if uploaded_file and '_dts' not in uploaded_file.name:
                                df = pd.read_csv(uploaded_file)
                                if 'Timedate' not in df.columns:
                                    continue
                                parts = uploaded_file.name.split('_')
                                if len(parts) < 2:
                                    continue
                                ip = parts[0]
                                try:
                                    bim = int(parts[1])
                                except ValueError:
                                    continue
                                board = next((b for b in boards if b['bim'] == bim), None)
                                if not board:
                                    insert_result = supabase.table("boards").insert({'lot_id': selected_lot_id, 'ip': ip, 'bim': bim, 'dut_sn': ''}).execute()
                                    board_id = insert_result.data[0]['id']
                                else:
                                    board_id = board['id']
                                    supabase.table("boards").update({'ip': ip}).eq('id', board_id).execute()
                                start = df['Timedate'].iloc[0]
                                end = df['Timedate'].iloc[-1]
                                cause = ''
                                if 'Pattern' in df.columns:
                                    last_pattern = df['Pattern'].iloc[-1]
                                    if pd.notna(last_pattern):
                                        cause = last_pattern
                                supabase.table('shutdowns').insert({
                                    'board_id': board_id,
                                    'start': start,
                                    'end': end,
                                    'cause': cause,
                                    'datalog': uploaded_file.name
                                }).execute()
                                update_shutdown_status(board_id, lot_info['required_duration'])
                        st.success("Datalogs processed")
                        st.rerun()

            with col_a4:
                if st.button("Export to CSV", key=f"export_{system_name}"):
                    output = io.StringIO()
                    writer = csv.writer(output)
                    writer.writerow(['IP', 'BIM', 'Lot', 'DUT_SN', 'Total Hours Ran', 'Progress', 'Running', 'Datalogs', 'Download Date', 'Start', 'End', 'Cause', 'Datalog', 'Duration Hours'])
                    for board in boards:
                        board_id = board['id']
                        operational = get_total_run_hours(board_id)
                        progress = get_progress(board_id, lot_info['required_duration'])
                        running = 'Yes' if is_running(board_id) else ''
                        datalog_cnt = supabase.table('shutdowns').select('*', count='exact').eq('board_id', board_id).not_.is_('datalog', 'null').execute().count
                        _, download = get_remaining_and_finish(board_id, lot_info['required_duration'], running == 'Yes')
                        shutdowns = supabase.table('shutdowns').select('*').eq('board_id', board_id).execute().data or [{}]
                        for shutdown in shutdowns:
                            duration = 0.0
                            start_dt = parse_custom_date(shutdown.get('start'))
                            end_dt = parse_custom_date(shutdown['end'])
                            if start_dt and end_dt:
                                duration = (end_dt - start_dt).total_seconds() / 3600.0
                            progress_str = f"{progress:.2f}%" if progress is not None else "N/A"
                            writer.writerow([
                                board['ip'],
                                board['bim'],
                                lot_info['lot_number'],
                                board['dut_sn'],
                                f"{operational:.2f}",
                                progress_str,
                                running,
                                datalog_cnt,
                                download,
                                shutdown.get('start', ''),
                                shutdown.get('end', '') or 'Ongoing',
                                shutdown.get('cause', ''),
                                shutdown.get('datalog', ''),
                                f"{duration:.2f}"
                            ])
                    st.download_button("Download CSV", output.getvalue(), file_name="export.csv", mime="text/csv", key=f"download_csv_{system_name}")

            # Edit BIMs (add/delete)
            if st.button("Edit BIMs", key=f"edit_bims_{system_name}"):
                with st.expander("Add BIM"):
                    ip = st.text_input("IP")
                    bim = st.number_input("BIM", min_value=1)
                    dut_sn = st.text_input("DUT SN")
                    if st.button("Add Board", key=f"add_board_{system_name}"):
                        if any(b['bim'] == bim for b in boards):
                            st.error("BIM already exists")
                        else:
                            supabase.table("boards").insert({'lot_id': selected_lot_id, 'ip': ip or f"IP{len(boards)+1:02d}", 'bim': bim, 'dut_sn': dut_sn}).execute()
                            st.rerun()

                if selected_rows:
                    if st.button("Delete Selected Board", key=f"delete_board_{system_name}"):
                        supabase.table("shutdowns").delete().eq("board_id", board_id).execute()
                        supabase.table("boards").delete().eq("id", board_id).execute()
                        st.rerun()

            # Scan DUT SNs (simplified as form)
            with st.expander("Scan DUT SNs"):
                start_bim = st.number_input("Start from BIM", min_value=1)
                sn = st.text_input("Scan SN (press enter to add)")
                if st.button("Add Scanned SN", key=f"add_scanned_{system_name}"):
                    board = next((b for b in boards if b['bim'] == start_bim), None)
                    if board:
                        supabase.table("boards").update({'dut_sn': sn}).eq('id', board['id']).execute()
                    else:
                        supabase.table("boards").insert({'lot_id': selected_lot_id, 'ip': f"IP{start_bim:02d}", 'bim': start_bim, 'dut_sn': sn}).execute()
                    st.rerun()

            # Auto-refresh timer (every 60s)
            st.markdown("""
                <script>
                setTimeout(function() {
                    window.location.reload();
                }, 60000);
                </script>
            """, unsafe_allow_html=True)

        else:
            st.info("No LOT selected. Add one to begin.")