vector<int> largestDivisibleSubset(vector<int>& nums) {
    sort(nums.begin(), nums.end());
    int n = nums.size();
    vector<int> dp(n, 1), dp_pred(n, -1);
    int max_size = 1, max_index = 0;

    for (int i = 0; i < n; i++) {
        for (int j = 0; j < i; j++) {
            if (nums[i] % nums[j] == 0 && dp[j] + 1 > dp[i]) {
                dp[i] = dp[j] + 1;
                dp_pred[i] = j;
                if (dp[i] > max_size) {
                    max_size = dp[i];
                    max_index = i;
                }
            }
        }
    }

    vector<int> subset;
    while (max_index != -1) {
        subset.push_back(nums[max_index]);
        max_index = dp_pred[max_index];
    }

    return subset;
}