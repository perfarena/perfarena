vector<vector<int>> mergeIntervals(vector<vector<int>>& intervals) {
    sort(intervals.begin(), intervals.end());
    
    vector<vector<int>> answer;
    
    for (int i = 0; i < intervals.size(); ++i) {
        if (answer.empty() || intervals[i][0] > answer.back()[1]) {
            answer.push_back(intervals[i]);
        } else {
            answer.back()[1] = max(answer.back()[1], intervals[i][1]);
        }
    }
    
    return answer;
}