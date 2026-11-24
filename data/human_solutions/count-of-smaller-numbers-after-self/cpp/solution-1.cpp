#include <vector>
using namespace std;

void merge(vector<pair<int, int>>& nums, int left, int mid, int right, vector<int>& count) {
    int i = left, j = mid + 1, k = 0;
    vector<pair<int, int>> tmp(right - left + 1);

    while (i <= mid && j <= right) {
        if (nums[i].second <= nums[j].second) {
            tmp[k++] = nums[i++];
            count[tmp[k - 1].first] += j - mid - 1;
        } else {
            tmp[k++] = nums[j++];
        }
    }

    while (i <= mid) {
        tmp[k++] = nums[i++];
        count[tmp[k - 1].first] += j - mid - 1;
    }

    while (j <= right) {
        tmp[k++] = nums[j++];
    }

    for (int p = 0; p < k; p++) {
        nums[left + p] = tmp[p];
    }
}

void mergeSort(vector<pair<int, int>>& nums, int left, int right, vector<int>& count) {
    if (left < right) {
        int mid = left + (right - left) / 2;
        mergeSort(nums, left, mid, count);
        mergeSort(nums, mid + 1, right, count);
        merge(nums, left, mid, right, count);
    }
}

vector<int> countSmaller(vector<int>& nums) {
    int n = nums.size();
    vector<pair<int, int>> v(n);
    for (int i = 0; i < n; i++) {
        v[i] = {i, nums[i]};
    }

    vector<int> count(n, 0);
    mergeSort(v, 0, n - 1, count);

    vector<int> result(n);
    for (int i = 0; i < n; i++) {
        result[v[i].first] = count[i];
    }

    return result;
}