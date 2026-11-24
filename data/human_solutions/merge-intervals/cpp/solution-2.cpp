// Sort intervals based on start time
sort(intervals.begin(), intervals.end());

vector<vector<int>> result;
int start = intervals[0][0];
int end = intervals[0][1];

for (int i = 1; i < intervals.size(); i++) {
    if (intervals[i][0] > end) {
        result.push_back({start, end});
        start = intervals[i][0];
        end = intervals[i][1];
    } else {
        end = max(end, intervals[i][1]);
    }
}

result.push_back({start, end});

return result;