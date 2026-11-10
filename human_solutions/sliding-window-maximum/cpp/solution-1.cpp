vector<int> maxSlidingWindow(vector<int>& nums, int k) {
    int n = nums.size();
    vector<int> answer;
    deque<int> Q;

    for (int i = 0; i < n; ++i) {
        while (!Q.empty() && nums[i] >= nums[Q.back()]) {
            Q.pop_back();
        }
        Q.push_back(i);

        if (i >= k - 1) {
            answer.push_back(nums[Q.front()]);

            if (i - Q.front() + 1 >= k) {
                Q.pop_front();
            }
        }
    }

    return answer;
}