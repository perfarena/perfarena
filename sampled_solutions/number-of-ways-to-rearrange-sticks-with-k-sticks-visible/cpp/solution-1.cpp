// Comparator function for sorting characters
bool compare(vector<int>& a, vector<int>& b) {
    if (a[0] == b[0]) {
        return a[1] > b[1]; // Sort by defense value in decreasing order
    }
    return a[0] < b[0]; // Sort by attack value in increasing order
}

int countWeakCharacters(vector<vector<int>>& properties) {
    sort(properties.begin(), properties.end(), compare); // Sort characters
    int count = 0;
    int maxDefense = properties.back()[1]; // Initialize maximum defense

    for (int i = properties.size() - 2; i >= 0; i--) {
        if (properties[i][1] < maxDefense) {
            count++; // Increment count for weak character
        } else {
            maxDefense = max(maxDefense, properties[i][1]); // Update maximum defense
        }
    }

    return count;
}