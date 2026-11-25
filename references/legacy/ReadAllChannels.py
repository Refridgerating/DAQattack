"""Historical four-channel NI-DAQmx prototype.

This file is retained as reference material only. It is not part of the
installed LabDAQ package and must not be imported by production code.
"""

import nidaqmx
from nidaqmx.constants import AcquisitionType
from nidaqmx.constants import READ_ALL_AVAILABLE, AcquisitionType
from nidaqmx.constants import Coupling


#Standard libraries
import pprint
import numpy as np
import pandas as pd
from datetime import datetime,date
import time

import csv
from openpyxl import Workbook
import plotly.express as px
import plotly.graph_objects as go

pp = pprint.PrettyPrinter(indent=4)

#open file for each channel to read and write data to. Read will be used for formatting purposes
filex = open("mydatax.txt", "+w")
filey = open("mydatay.txt", "+w")
filez = open("mydataz.txt", "+w")
filep = open("mydatap.txt", "+w")

def main():




    with nidaqmx.Task() as task:
        
        cont_mode = AcquisitionType.CONTINUOUS
        sample_rate = 20000
        inc = 1/sample_rate
        #samples_to_acq = sample_rate will prevent write operation from falling behind data_read task
        samples_to_acq = sample_rate

        #Include sampling rate in data file so can create time vector later
      #  filex.write("sample rate:" + str(sample_rate) + "\n")
      #  filey.write("sample rate:" + str(sample_rate) + "\n")
      #  filez.write("sample rate:" + str(sample_rate) + "\n")
      #  filep.write("sample rate:" + str(sample_rate) + "\n")
        
        #Name NIDAQ channels
        ch1i = 'Dev1/ai0'
        ch2i = 'Dev1/ai1'
        ch3i = 'Dev1/ai2'
        ch4i = 'Dev1/ai3'

        #initiate channels
        chan=task.ai_channels.add_ai_voltage_chan(ch1i)
        task.ai_channels.add_ai_voltage_chan(ch2i)
        task.ai_channels.add_ai_voltage_chan(ch3i)
        task.ai_channels.add_ai_voltage_chan(ch4i)

        #chan.ai_coupling = Coupling.DC


        units_g = nidaqmx.constants.AccelUnits.G
        
        #Sample clock timing
        task.timing.cfg_samp_clk_timing(sample_rate, sample_mode=cont_mode, samps_per_chan=samples_to_acq)
        #task.timing.cfg_samp_clk_timing(sample_rate, sample_mode=cont_mode)
        
        #start NIDAQ task
        task.start()

        print("Running task. Press Ctrl+C to stop.")

        try:
            total_read = 0
            i=0
            while True:
                timeString = str(i/sample_rate)
                incString = str(inc)
                data = task.read(number_of_samples_per_channel=samples_to_acq)
                x = data[:1]
                y = data[1:2]
                z = data[2:3]
                p = data[3:4]

                #dataString = str(data)[1:-1]
                for value in x:
                    dataStringx = str(value)[1:-1]
                    new_string = dataStringx.replace(", ","\n")
                    filex.write(new_string + "\n")

                for value in y:
                    dataStringy = str(value)[1:-1]
                    new_string = dataStringy.replace(", ","\n")
                    filey.write(new_string + "\n")

                for value in z:
                    dataStringz = str(value)[1:-1]
                    new_string = dataStringz.replace(", ","\n")
                    filez.write(new_string + "\n")

                for value in p:
                    dataStringp = str(value)[1:-1]
                    new_string = dataStringp.replace(", ","\n")
                    filep.write(new_string + "\n")

                #i += samples_to_acq
                total_read += samples_to_acq
                #print(x)
        except KeyboardInterrupt:
            pass
        finally:
            task.stop()
            #lines = [line.replace(',', '\n') for line in filex]
            print(f"\nAcquired {total_read} total samples.")
            filex.close()


            yfile = open("mydatax.txt", "r")
            ydata_string = yfile.read() 
            ydata_p1 = ydata_string.split("\n") 
            #ydata = ydata_p1.pop()
            xdata = np.linspace(0, total_read/sample_rate, total_read+1)
            print(ydata_p1)

        fig = px.line(
            x=xdata,
            y=ydata_p1
        )

        fig.show()

        #task.stop()

if __name__ == "__main__":
    main()
