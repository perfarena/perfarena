vector<vector<int>> intervals;

void addNum(int val) {
    int index = lower_bound(intervals.begin(), intervals.end(), vector<int>{val, val}) - intervals.begin();
    
    if (index > 0 && intervals[index - 1][1] >= val - 1) {
        index--;
    }
    
    int start = val, end = val;
    
    while (index < intervals.size() && intervals[index][0] <= val + 1 && intervals[index][1] >= val - 1) {
        start = min(start, intervals[index][0]);
        end = max(end, intervals[index][1]);
        intervals.erase(intervals.begin() + index);
    }
    
    intervals.insert(intervals.begin() + index, {start, end});
}

vector<vector<int>> getIntervals() {
    return intervals;
}