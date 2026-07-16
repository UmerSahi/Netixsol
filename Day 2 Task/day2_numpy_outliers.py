import numpy as np

#creating synthetic data
sensor_data= np.array([10,12,14,16,19,18,11,30])
print("Sensor data ="+str(sensor_data))

#rolling mean
window = 3

print("\nRolling Mean:")

for i in range(len(sensor_data) - window + 1):
    current_window = sensor_data[i:i+window]
    mean = np.mean(current_window)
    print(mean)

#rolling standard deviation
print("\nRolling Standard Deviation:")

for i in range(len(sensor_data) - window + 1):
    current_window = sensor_data[i:i+window]
    std = np.std(current_window)
    print(std)

# z score normalize
mean = np.mean(sensor_data)
std = np.std(sensor_data)
z_scores = (sensor_data - mean) / std
print("\nZ-Scores:")
print(z_scores)

#outliers
print("\nOutliers (|z| > 2):")

for i in range(len(sensor_data)):
    if abs(z_scores[i]) > 2:
        print("Index:", i)
        print("Value:", sensor_data[i])
        print("Z-Score:", z_scores[i])