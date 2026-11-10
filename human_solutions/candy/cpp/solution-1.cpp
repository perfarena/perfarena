#include <iostream>
#include <vector>
#include <numeric>

int minCandies(std::vector<int>& ratings) {
    int n = ratings.size();
    std::vector<int> answer(n, 1);

    for (int i = 1; i < n; ++i) {
        if (ratings[i] > ratings[i - 1]) {
            answer[i] = answer[i - 1] + 1;
        }
    }

    for (int i = n - 2; i >= 0; --i) {
        if (ratings[i] > ratings[i + 1]) {
            answer[i] = std::max(answer[i], answer[i + 1] + 1);
        }
    }

    return std::accumulate(answer.begin(), answer.end(), 0);
}

int main() {
    std::vector<int> ratings = {1, 2, 2};
    std::cout << "Minimum number of candies: " << minCandies(ratings) << std::endl;

    return 0;
}