#!/bin/bash
# 🔍 Crypto Job Hunter - Manual Run Script

echo ""
echo "🔍 Crypto Job Hunter"
echo "==================="
echo ""

# Activate virtual environment
source .venv/bin/activate

# Run the job hunter
echo "⏳ Scanning job boards..."
echo ""

# Run and capture output
python3 job_hunter.py 2>&1 | tee .last_run.log

echo ""
echo "==================="

# Find and open the latest HTML report
LATEST_REPORT=$(ls -t job_report_*.html 2>/dev/null | head -1)

if [ -n "$LATEST_REPORT" ]; then
    echo "✅ Opening report in browser..."
    open "$LATEST_REPORT"
    echo ""
    echo "📊 Report opened: $LATEST_REPORT"
    echo "   • All job titles are clickable links"
    echo "   • Jobs are sorted by score"
    echo "   • New jobs are highlighted"
else
    echo "📝 Check the console output above for results"
fi

echo ""
echo "📁 Files:"
echo "   • job_report.html - Full report with clickable links"
echo "   • jobs.db - Job history database"
echo "   • .last_run.log - Output from this run"
echo ""