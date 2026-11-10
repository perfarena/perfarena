// Define the private member variables in the class
int capacity;
list<int> keys;
unordered_map<int, pair<int, list<int>::iterator>> map;

// Implement the constructor
LRUCache(int capacity) : capacity(capacity) {}

// Implement the get method
int get(int key) {
    if (map.find(key) != map.end()) {
        keys.erase(map[key].second);
        keys.push_front(key);
        map[key].second = keys.begin();
        return map[key].first.first;
    }
    return -1;
}

// Implement the put method
void put(int key, int value) {
    if (map.find(key) != map.end()) {
        keys.erase(map[key].second);
    } else if (keys.size() >= capacity) {
        int leastRecent = keys.back();
        keys.pop_back();
        map.erase(leastRecent);
    }
    keys.push_front(key);
    map[key] = {value, keys.begin()};
}