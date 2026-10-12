sort(box_types.begin(), box_types.end(), [](vector<int>& a, vector<int>& b) {
    return a[1] >= b[1];
});