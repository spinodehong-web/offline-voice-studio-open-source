"""Retain speech tails while matching sentence pauses to a local delivery profile."""
import re
import numpy as np


def shape_sentence_pauses(signal, rate, words, target_gap=.68, max_gap=2, targets=None):
    edits=[];report=[]
    for before,after in zip(words,words[1:]):
        if not re.search(r'[.!?]$', before['text'].strip()):continue
        gap=after['start']-before['end']
        desired=(targets or {}).get(before['text'].strip(), target_gap)
        delta=desired-gap
        if abs(delta)<.025 or gap<.12 or gap>max_gap:continue
        lo=max(0,round((before['end']+.03)*rate));hi=min(len(signal),round((after['start']-.03)*rate))
        frame=max(1,round(.01*rate));quiet=[];start=None
        for i in range(lo,hi-frame+1,frame):
            silent=float(np.sqrt(np.mean(signal[i:i+frame].astype('float64')**2)))<.003
            if silent and start is None:start=i
            if not silent and start is not None:quiet.append((start,i));start=None
        if start is not None:quiet.append((start,hi))
        if not quiet:continue
        a,b=max(quiet,key=lambda x:x[1]-x[0]);width=b-a
        if width<round(.06*rate):continue
        center=(a+b)//2
        if delta>0:
            count=round(min(delta,.4)*rate);edits.append((center,center,np.zeros(count,dtype='float32')));actual=count/rate
        else:
            # Remove only confirmed low-energy samples, preserving at least 60 ms.
            count=min(round(-delta*rate),max(0,width-round(.06*rate)))
            if count<=0:continue
            edits.append((center-count//2,center+(count-count//2),np.empty(0,dtype='float32')));actual=-count/rate
        report.append({'after':before['text'],'original_gap':gap,'adjustment_seconds':actual,'target_gap':desired})
    result=np.asarray(signal,dtype='float32')
    for a,b,replacement in sorted(edits,reverse=True,key=lambda x:x[0]):result=np.concatenate((result[:a],replacement,result[b:]))
    return result,report
