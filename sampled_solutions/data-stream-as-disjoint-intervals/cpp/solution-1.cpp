#include <vector>
using namespace std;

class SummaryRanges {
private:
    vector<pair<int, int>> intervals;

public:
    void addNum(int val) {
        int left = 0, right = intervals.size() - 1;
        
        while (left <= right) {
            int mid = left + (right - left) / 2;
            if (intervals[mid].first <= val && val <= intervals[mid].second) {
                return; // Interval already covered
            } else if (intervals[mid].second + 1 == val) {
                intervals[mid].second = val; // Merge with the next interval
                return;
            } else if (intervals[mid].first - 1 == val) {
                intervals[mid].first = val; // Merge with the previous interval
                return;
            } else if (intervals[mid].first > val) {
                right = mid - 1;
            } else {
                left = mid + 1;
            }
        }
        
        intervals.insert(intervals.begin() + left, {val, val});
    }

    vector<pair<int, int>> getIntervals() {
        for (int i = 0; i < intervals.size() - 1; ++i) {
            if (intervals[i].second + 1 == intervals[i + 1].first) {
                intervals[i].second = intervals[i + 1].second;
                intervals.erase(intervals.begin() + i + 1);
                --i; // Recheck merged interval
            }
        }
        return intervals;
    }
};