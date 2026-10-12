#include <vector>
#include <unordered_map>
#include <algorithm>

std::vector<int> dailyTemperatures(std::vector<int>& temperatures) {
    int n = temperatures.size();
    std::vector<int> result(n, 0);
    std::unordered_map<int, int> lastSeen;

    for (int i = n - 1; i >= 0; i--) {
        for (int other = temperatures[i] + 1; other <= 100; other++) {
            if (lastSeen.find(other) != lastSeen.end()) {
                int meanDays = std::min(meanDays, lastSeen[other] - i);
            }
        }
        if (meanDays != INT_MAX) {
            result[i] = meanDays;
        }
        lastSeen[temperatures[i]] = i;
    }

    return result;
}