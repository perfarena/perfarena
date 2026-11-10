#include <queue>
#include <vector>

int lastStoneWeight(std::vector<int>& stones) {
    std::priority_queue<int> pq(stones.begin(), stones.end());

    while (pq.size() > 1) {
        int x = pq.top();
        pq.pop();
        int y = pq.top();
        pq.pop();

        if (x != y) {
            pq.push(y - x);
        }
    }

    return pq.empty() ? 0 : pq.top();
}